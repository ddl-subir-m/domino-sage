from pathlib import Path

import json

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """CHAT so existing Chat tests do not trip the fail-safe (unreadable → suggest)."""

    def __init__(self, verdict: str = "CHAT"):
        self.verdict = verdict
        self.seen: list = []

    def route(self, request, labels):
        self.seen.append((request, labels))
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _orch(tmp: Path, turns: list[Turn] | None = None, gateway=None, project_id: str = "Sage"):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns or [])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=gateway or ScriptedGateway(),
                        catalog=_catalog(), project_id=project_id, feedback=OkFeedback(),
                        opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def test_chat_opencode_session_is_not_the_react_app(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    work = orch.project(start_preview=False).workspace.path / ".sage" / "chat-work"
    assert oc.sessions[0]["directory"] == str(work)
    assert (work / "AGENTS.md").exists()
    assert (work / "examples").is_symlink()


def test_chat_does_not_seed_the_react_template(tmp_path: Path):
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="hello")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    assert not (ws / "package.json").exists()
    assert not (ws / "src").exists()
    assert oc.sessions[0]["directory"] == str(ws / ".sage" / "chat-work")


def test_chat_prompt_keeps_at_name_and_attaches_the_file(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    ws = orch.project(start_preview=False).workspace.path
    path = ".sage/scratch/desk.csv"
    dest = ws / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("desk,notional\nRates,10\n")
    orch.add_thread_context(tid, {"kind": "file", "name": "desk.csv", "path": path})
    list(orch.chat_stream(tid, "what data is there in @desk.csv"))
    prompt = oc.prompts[0]["text"]
    assert prompt.endswith("what data is there in @desk.csv") or "what data is there in @desk.csv" in prompt
    atts = oc.prompts[0]["attachments"]
    assert atts and atts[0]["name"] == "desk.csv"
    assert atts[0]["path"] == path
    assert "Rates,10" not in prompt
    assert "(@desk.csv)" in prompt
    assert "@name in the user's message" in prompt


def test_chat_turn_uses_sage_chat_skips_plan_and_tsc(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Rates is the largest desk.")])
    thread = orch.create_thread()
    events = list(orch.chat_stream(thread["id"], "what's our gross exposure by desk?"))

    assert oc.prompts[0]["agent"] == "sage-chat"
    kinds = {e.get("type") for e in events}
    assert "plan-proposed" not in kinds
    assert "typecheck" not in kinds
    assert next(e for e in events if e["type"] == "done")["decision"] == "answered"
    assert orch.project(start_preview=False).workspace.read_history() == []


def test_chat_turn_records_artifact_and_reverts_src(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    thread = orch.create_thread()
    tid = thread["id"]
    table = '{"title": "Desks", "columns": ["desk"], "rows": [["Rates"]]}'
    oc.turns = [Turn(
        text="Here is gross notional by desk.",
        writes={
            "src/App.tsx": "// hijack",
            f"examples/{tid}/exposure.table.json": table,
        },
    )]
    src = orch.project(start_preview=False).workspace.path / "src" / "App.tsx"
    original = src.read_text()
    events = list(orch.chat_stream(tid, "what's in this CSV?"))

    assert src.read_text() == original
    arts = next(e for e in events if e.get("type") == "artifacts")["items"]
    assert arts[0]["kind"] == "table"
    assert arts[0]["path"] == f"examples/{tid}/exposure.table.json"
    assert (orch.project(start_preview=False).workspace.path / arts[0]["path"]).read_text() == table
    hist = orch.thread_history(tid)
    assert hist[0]["type"] == "user"
    ctx = orch.thread_context(tid)["items"]
    assert not any(i.get("kind") == "artifact" for i in ctx)
    assert orch.get_thread(tid)["artifacts"][0]["path"] == arts[0]["path"]
    assert orch.project(start_preview=False).workspace.read_history() == []
    assert not any(e.get("kind") == "tool" for e in events)
    assert any(e.get("type") == "artifacts" for e in hist)


def test_followup_lists_written_artifacts_without_chipping_them(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    path = f"examples/{tid}/desks.png"
    oc.turns = [
        Turn(text="Charted.", writes={path: "png"}),
        Turn(text="Bluer."),
    ]
    list(orch.chat_stream(tid, "chart desks"))
    list(orch.chat_stream(tid, "make the bars blue"))
    prompt = oc.prompts[1]["text"]
    assert "Already written this Thread" in prompt
    assert path in prompt
    assert "served URL" not in prompt
    assert not any(i.get("kind") == "artifact" for i in orch.thread_context(tid)["items"])


def test_chat_does_not_record_bash_on_the_thread(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(
        text="Here is a sine wave.",
        tools=["bash"],
        writes={"src/examples/SineWaveChart.tsx": "// no"},
    )]
    events = list(orch.chat_stream(tid, "generate a dummy chart that has a sine wave"))
    assert [e["tool"] for e in events if e.get("kind") == "tool"] == ["bash"]
    hist = orch.thread_history(tid)
    assert [e.get("tool") for e in hist if e.get("kind") == "tool"] == []


def test_chat_user_event_snapshots_context_chips(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(text="Rates is the largest desk.")])
    tid = orch.create_thread()["id"]
    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "positions.csv", "path": "public/data/positions.csv",
    })
    events = list(orch.chat_stream(tid, "summarise this"))
    user = next(e for e in events if e["type"] == "user")
    assert user["contextIds"] == [row["id"]]
    assert user["context"][0]["name"] == "positions.csv"
    orch.remove_thread_context(tid, row["id"])
    hist = orch.thread_history(tid)
    assert hist[0]["contextIds"] == [row["id"]]
    assert hist[0]["context"][0]["name"] == "positions.csv"
    assert orch.thread_context(tid)["items"] == []


def test_chat_followup_does_not_replay_the_prior_reply(tmp_path: Path):
    # OpenCode returns the whole session on every poll. Without a seen-baseline, a follow-up
    # re-emits the previous greeting at the top of the new Sage bubble.
    orch, _ = _orch(tmp_path, [
        Turn(text="Hi there! What would you like to build today?"),
        Turn(text="Autodoc is a dataset of model documents."),
    ])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    events = list(orch.chat_stream(tid, "whats in autodoc"))
    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    assert texts == ["Autodoc is a dataset of model documents."]


def test_chat_keeps_only_the_last_assistant_text(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(
        prelude="The examples directory is at the chat-work root. Let me save there",
        text="Rates is the largest desk.",
    )])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "chart this"))
    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    assert texts == ["Rates is the largest desk."]
    hist = orch.thread_history(tid)
    assert [e.get("text") for e in hist if e.get("kind") == "text"] == ["Rates is the largest desk."]


def test_chat_prompt_tells_the_agent_an_unmounted_dataset_is_not_the_git_repo(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "dataset", "name": "autodoc", "project": "Sage"})
    list(orch.chat_stream(tid, "whats in autodoc"))
    prompt = oc.prompts[0]["text"]
    assert "autodoc" in prompt
    assert "not mounted" in prompt
    assert "not this Dataset" in prompt
    assert "Do not greet by asking what to build" in prompt
    assert f"examples/{tid}/" in prompt
    assert "not a React file" in prompt


def test_chat_turn_arms_web_when_the_prompt_has_a_url(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="The page lists three desks.")])
    armed = []
    orig = oc.send_prompt

    def wrap(session_id, text, model=None, agent=None, attachments=None, **kwargs):
        armed.append(orch.project(start_preview=False).control.snapshot().web_allowed)
        return orig(session_id, text, model=model, agent=agent, attachments=attachments, **kwargs)

    oc.send_prompt = wrap
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "summarise https://example.com/rates"))
    assert armed == [True]
    assert "URL https://example.com/rates" in oc.prompts[0]["text"]
    assert "Read this page" in oc.prompts[0]["text"]
    assert orch.project(start_preview=False).control.snapshot().web_allowed is False


def test_chat_turn_does_not_arm_web_for_an_ordinary_question(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Rates is the largest desk.")])
    armed = []
    orig = oc.send_prompt

    def wrap(session_id, text, model=None, agent=None, attachments=None, **kwargs):
        armed.append(orch.project(start_preview=False).control.snapshot().web_allowed)
        return orig(session_id, text, model=model, agent=agent, attachments=attachments, **kwargs)

    oc.send_prompt = wrap
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what's our gross exposure by desk?"))
    assert armed == [False]
    assert "URL " not in oc.prompts[0]["text"]


def test_chat_followup_still_arms_web_after_a_url_turn(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="The page lists three desks."),
        Turn(text="Rates is still the largest."),
    ])
    armed = []
    orig = oc.send_prompt

    def wrap(session_id, text, model=None, agent=None, attachments=None, **kwargs):
        armed.append(orch.project(start_preview=False).control.snapshot().web_allowed)
        return orig(session_id, text, model=model, agent=agent, attachments=attachments, **kwargs)

    oc.send_prompt = wrap
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "summarise https://example.com/rates"))
    list(orch.chat_stream(tid, "which desk is largest?"))
    assert armed == [True, True]
    assert "URL https://example.com/rates" in oc.prompts[1]["text"]


def test_chat_prompt_points_at_mounted_dataset_files(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "dataset", "name": "autodoc", "path": "/mnt/data/autodoc", "project": "Sage",
    })
    list(orch.chat_stream(tid, "whats in autodoc"))
    prompt = oc.prompts[0]["text"]
    assert "files at /mnt/data/autodoc" in prompt
    assert "not mounted" not in prompt


def test_chat_prompt_describes_a_file_chip_without_dumping_rows(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    ws = orch.project(start_preview=False).workspace.path
    path = ".sage/scratch/desk.csv"
    dest = ws / path
    dest.parent.mkdir(parents=True)
    dest.write_text("desk,notional\nRates,10\n")
    orch.add_thread_context(tid, {"kind": "file", "name": "desk.csv", "path": path})
    list(orch.chat_stream(tid, "summarise this"))
    prompt = oc.prompts[0]["text"]
    assert "desk.csv" in prompt
    assert "csv" in prompt.lower()
    assert "Rates,10" not in prompt


def test_chat_prompt_names_a_scoped_table_and_its_columns(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    row = orch.add_thread_context(tid, {
        "kind": "data_source",
        "name": "Snowflake-Data-Warehouse",
        "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
        "bindingKey": ["data_source", "ds-dwh"],
        "parentId": "data_source:ds-dwh",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    assert any(c["name"] == "ACCOUNT_ID" for c in row.get("columns") or [])
    list(orch.chat_stream(tid, "what is in DIM_ACCOUNT"))
    prompt = oc.prompts[0]["text"]
    assert "table DWH.MARTS.DIM_ACCOUNT" in prompt
    assert "ACCOUNT_ID" in prompt
    assert "cannot query it live" not in prompt


def test_chat_prompt_for_an_unmounted_dataset_file_does_not_search_git(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "file",
        "name": "positions.csv",
        "datasetId": "ds_missing",
        "datasetRelPath": "positions.csv",
        "datasetName": "autodoc",
    })
    list(orch.chat_stream(tid, "whats in positions"))
    prompt = oc.prompts[0]["text"]
    assert "not mounted" in prompt
    assert "Do not search this git repo" in prompt


def test_new_conversation_does_not_provision(tmp_path: Path, monkeypatch):
    orch, _ = _orch(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr("sage.provision.service.HubService.create_app",
                        lambda *a, **k: calls.append(1))
    first = orch.create_thread()
    second = orch.create_thread()
    assert first["id"] != second["id"]
    assert calls == []
    assert len(orch.list_threads()) == 2


def test_get_patch_delete_thread(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    row = orch.create_thread()
    got = orch.get_thread(row["id"])
    assert got["id"] == row["id"]
    assert got["history"] == []
    patched = orch.patch_thread(row["id"], {"title": "Rates", "pinned": True})
    assert patched["title"] == "Rates"
    assert patched["pinned"] is True
    orch.delete_thread(row["id"])
    assert orch.list_threads() == []


def _track_saves(orch):
    calls = []

    def fake(project, prompt):
        calls.append(prompt)
        return {"type": "saved", "ok": True, "pushed": True, "detail": prompt}

    orch._save_to_git = fake
    return calls


def test_chat_first_turn_saves(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(text="Rates is the largest desk.")])
    calls = _track_saves(orch)
    thread = orch.create_thread()
    events = list(orch.chat_stream(thread["id"], "what's our gross exposure by desk?"))

    assert calls == ["chat (first)"]
    saved = next(e for e in events if e["type"] == "saved")
    assert saved["ok"] is True
    assert orch._chat_dirty is False
    assert orch._chat_save_timer is None


def test_chat_text_followup_saves_on_idle_not_every_turn(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Rates."), Turn(text="And by region, APAC.")])
    calls = _track_saves(orch)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what's our gross exposure by desk?"))
    calls.clear()

    events = list(orch.chat_stream(tid, "and by region?"))
    assert calls == []
    assert not any(e.get("type") == "saved" for e in events)
    assert orch._chat_dirty is True
    assert orch._chat_save_timer is not None

    orch._cancel_chat_idle_save()
    orch._on_chat_save_idle()
    assert calls == ["chat (idle)"]
    assert orch._chat_dirty is False
    assert orch._chat_save_timer is None


def test_chat_artifact_followup_saves_immediately(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    calls = _track_saves(orch)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hello"))
    calls.clear()

    table = '{"title": "Desks", "columns": ["desk"], "rows": [["Rates"]]}'
    oc.turns.append(Turn(
        text="Here is gross notional by desk.",
        writes={f"examples/{tid}/exposure.table.json": table},
    ))
    events = list(orch.chat_stream(tid, "what's in this CSV?"))

    assert calls == ["chat (artifacts)"]
    assert next(e for e in events if e["type"] == "saved")["ok"] is True
    assert orch._chat_dirty is False


def test_chat_leave_thread_flushes(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Rates."), Turn(text="Still Rates.")])
    calls = _track_saves(orch)
    a = orch.create_thread()
    b = orch.create_thread()
    list(orch.chat_stream(a["id"], "what's our gross exposure by desk?"))
    calls.clear()
    list(orch.chat_stream(a["id"], "say more"))
    assert calls == []

    orch.get_thread(a["id"])
    assert calls == []

    orch.get_thread(b["id"])
    assert calls == ["chat (leave)"]
    assert orch._chat_dirty is False

    calls.clear()
    oc.turns.append(Turn(text="APAC."))
    list(orch.chat_stream(a["id"], "and by region?"))
    assert orch._chat_dirty is True
    orch.create_thread()
    assert calls == ["chat (leave)"]


def test_flush_chat_save_and_shutdown_cancel_idle(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Rates."), Turn(text="Still Rates.")])
    calls = _track_saves(orch)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what's our gross exposure by desk?"))
    calls.clear()
    list(orch.chat_stream(tid, "say more"))
    assert orch._chat_save_timer is not None

    assert orch.flush_chat_save()["ok"] is True
    assert calls == ["chat (leave)"]
    assert orch._chat_dirty is False
    assert orch._chat_save_timer is None

    oc.turns.append(Turn(text="APAC."))
    list(orch.chat_stream(tid, "and by region?"))
    calls.clear()
    assert orch._chat_save_timer is not None
    orch.shutdown()
    assert orch._chat_save_timer is None
    assert calls == ["save before stop"]


def test_analysis_turns_do_not_suggest_handoff(tmp_path: Path):
    gw = ScriptedGateway("CHAT")
    orch, oc = _orch(tmp_path, [
        Turn(text="Rates."),
        Turn(text="By region, APAC."),
        Turn(text="Still Rates."),
    ], gateway=gw)
    tid = orch.create_thread()["id"]
    for prompt in ("what's our gross exposure by desk?", "and by region?", "say more"):
        events = list(orch.chat_stream(tid, prompt))
        assert not any(e.get("type") == "handoff-suggest" for e in events)
    assert orch.get_thread(tid)["handoff"] is None
    assert len(gw.seen) == 3


def test_app_shaped_turn_suggests_handoff_once(tmp_path: Path):
    gw = ScriptedGateway("CHAT")
    orch, oc = _orch(tmp_path, [
        Turn(text="Rates is the largest desk."),
        Turn(text="I can sketch a dashboard."),
        Turn(text="Colleagues could open that."),
    ], gateway=gw)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what's our gross exposure by desk?"))
    list(orch.chat_stream(tid, "and by region?"))
    assert orch.get_thread(tid)["handoff"] is None

    gw.verdict = "APP"
    events = list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    suggest = next(e for e in events if e.get("type") == "handoff-suggest")
    assert suggest["reason"] == "classifier"
    row = orch.get_thread(tid)["handoff"]
    assert row["status"] == "suggested"
    assert row["suggestedAt"]
    assert row["suppressed"] is False
    calls_after_hit = len(gw.seen)

    gw.verdict = "APP"
    oc.turns.append(Turn(text="More numbers."))
    later = list(orch.chat_stream(tid, "and by product?"))
    assert not any(e.get("type") == "handoff-suggest" for e in later)
    assert len(gw.seen) == calls_after_hit
    assert orch.get_thread(tid)["handoff"]["suggestedAt"] == row["suggestedAt"]


def test_explicit_build_request_skips_classifier(tmp_path: Path):
    gw = ScriptedGateway("CHAT")
    orch, _ = _orch(tmp_path, [Turn(text="Ok.")], gateway=gw)
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "build me a dashboard"))
    suggest = next(e for e in events if e.get("type") == "handoff-suggest")
    assert suggest["reason"] == "explicit"
    assert gw.seen == []
    assert orch.get_thread(tid)["handoff"]["status"] == "suggested"


def test_not_now_suppresses_and_classifier_does_not_run_again(tmp_path: Path):
    gw = ScriptedGateway("APP")
    orch, oc = _orch(tmp_path, [Turn(text="A dashboard."), Turn(text="More.")], gateway=gw)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    patched = orch.patch_thread(tid, {"handoff": "suppress"})
    assert patched["id"] == tid
    row = orch.get_thread(tid)["handoff"]
    assert row["suppressed"] is True
    assert row["status"] == "suppressed"
    calls = len(gw.seen)
    later = list(orch.chat_stream(tid, "and by region?"))
    assert len(gw.seen) == calls
    assert not any(e.get("type") == "handoff-suggest" for e in later)
    assert orch.get_thread(tid)["handoff"]["status"] == "suppressed"


_PLAN = (
    "A desk exposure dashboard.\n\n"
    "## Plan\n"
    "1. **Desk table** — Show notional by desk.\n"
    "2. **Chart** — Use the example PNG.\n\n"
    "## Open questions\n"
    "None — ready to build.\n"
)


def test_write_a_plan_runs_sage_plan_and_opens_sheet_payload(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="Rates is the largest desk."),
        Turn(text=_PLAN),
    ])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    result = orch.draft_handoff_plan(tid)

    assert oc.prompts[-1]["agent"] == "sage-plan"
    assert result["ok"] is True
    assert result["handoff"]["status"] == "planned"
    assert result["plan"].startswith("A desk exposure dashboard.")
    ws = orch.project(start_preview=False).workspace
    assert (ws.path / ".sage" / "plan.md").read_text().startswith("A desk exposure dashboard.")
    assert "Asked:" in (ws.path / ".sage" / "handoff.md").read_text()
    hist = ws.read_history()
    assert any(e.get("type") == "plan-proposed" for e in hist)
    src = ws.path / "src" / "App.tsx"
    assert "return null" in src.read_text()

    again = orch.draft_handoff_plan(tid)
    assert again["handoff"]["status"] == "planned"
    assert sum(1 for p in oc.prompts if p["agent"] == "sage-plan") == 1


def test_confirm_handoff_writes_files_and_bindings_not_src(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="Rates."),
        Turn(text=_PLAN),
    ])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "trades",
        "bindingKey": ["data_source", "ds-trades"],
    })
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)
    src = orch.project(start_preview=False).workspace.path / "src" / "App.tsx"
    before = src.read_text()
    orch.project(start_preview=False).workspace.mark_untitled(True)

    result = orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})
    assert result["ok"] is True
    assert result["handoff"]["status"] == "bound"
    ws = orch.project(start_preview=False).workspace
    handoff_md = (ws.path / ".sage" / "handoff.md").read_text()
    assert "trades" in handoff_md
    assert "The plan is what to build" in handoff_md
    assert not (ws.path / ".sage" / "handoff-transcript.md").exists()
    bindings = ws.read_bindings()
    assert any(b.get("id") == "ds-trades" for b in bindings)
    assert src.read_text() == before
    assert ws.is_untitled() is False
    assert result["untitled"] is False
    assert result["title"] == "A desk exposure dashboard."
    assert ws.display_name() == "A desk exposure dashboard."

    orch.confirm_handoff(tid, {"transcript": True})
    assert (ws.path / ".sage" / "handoff-transcript.md").exists()


def test_empty_plan_does_not_mark_planned(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(text="Rates."), Turn(text="")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a dashboard"))
    with pytest.raises(ValueError, match="empty plan"):
        orch.draft_handoff_plan(tid)
    assert (orch.get_thread(tid)["handoff"] or {}).get("status") != "planned"


def test_scratch_slug_hydrates_untitled_chip(tmp_path: Path, monkeypatch):
    from sage.provision import naming

    monkeypatch.setenv("DOMINO_USER_NAME", "alice")
    monkeypatch.setenv("DOMINO_USER_ID", "507f1f77bcf86cd799439011")
    slug = naming.untitled_project_name("alice", "507f1f77bcf86cd799439011")
    orch, _ = _orch(tmp_path, project_id=slug)
    project = orch.project(start_preview=False)
    assert project.workspace.is_untitled() is True
    assert project.status()["untitled"] is True
    assert project.status()["name"] == "Untitled"


def test_named_project_does_not_hydrate_untitled(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert project.workspace.is_untitled() is False
    assert project.status()["name"] == "Sage"
