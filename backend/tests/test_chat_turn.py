from pathlib import Path

import json
import time

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


def _orch(tmp: Path, turns: list[Turn] | None = None, gateway=None, project_id: str = "Sage",
          client=None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = client(ws) if client is not None else FakeOpenCode(ws, turns or [])
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


def test_chat_prompt_routes_an_unmounted_dataset_to_the_data_library(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(
        tid, {"kind": "dataset", "id": "dataset:abc123", "name": "autodoc", "project": "Sage"}
    )
    list(orch.chat_stream(tid, "whats in autodoc"))
    prompt = oc.prompts[0]["text"]
    assert "autodoc" in prompt
    # Both halves of the identifier: the data library rejects a bare name and a bare id alike.
    assert 'get_dataset("dataset-autodoc-abc123")' in prompt
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
    # The panel pins a TABLE, so the chip's name is the table — this is the payload the Workbench
    # actually posts. The source's own name has to be resolved from the chip's parent.
    row = orch.add_thread_context(tid, {
        "kind": "data_source",
        "name": "DIM_ACCOUNT",
        "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
        "bindingKey": ["data_source", "ds-dwh"],
        "parentId": "data_source:ds-dwh",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    assert row["sourceName"] == "Snowflake-Data-Warehouse"
    assert any(c["name"] == "ACCOUNT_ID" for c in row.get("columns") or [])
    list(orch.chat_stream(tid, "what is in DIM_ACCOUNT"))
    prompt = oc.prompts[0]["text"]
    assert "table DWH.MARTS.DIM_ACCOUNT" in prompt
    assert "ACCOUNT_ID" in prompt
    assert "cannot query it live" not in prompt
    assert "DataSourceClient" in prompt
    # get_datasource() takes the SOURCE. Passing the table name is what produced the live
    # "no Data Source registered under that name" against a source called BigQuery_Demo.
    assert "get_datasource('Snowflake-Data-Warehouse')" in prompt
    assert "get_datasource('DIM_ACCOUNT')" not in prompt


def test_chat_will_not_hand_out_a_query_recipe_it_cannot_name_the_source_for(tmp_path: Path):
    # A chip that lost its parent leaves only the table name, and guessing with it sends the agent
    # at a lookup that fails as "no Data Source registered under that name" — which reads like the
    # person attached the wrong thing.
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source",
        "name": "DIM_ACCOUNT",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    list(orch.chat_stream(tid, "what is in DIM_ACCOUNT"))
    prompt = oc.prompts[0]["text"]
    assert "cannot query it live" in prompt
    assert "get_datasource" not in prompt


def test_chat_turn_times_out_a_hung_opencode_session(tmp_path: Path):
    """Running is not the same as working: the session never goes idle and never says anything."""
    orch, oc = _orch(tmp_path, [Turn(text="never emitted")])
    oc.stay_running = True
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "what is in clickstream", timeout_s=0.05))
    assert oc.interrupted == 1
    err = next(e for e in events if e["type"] == "error")
    assert "stopped making progress" in err["message"]
def test_an_explicit_build_request_does_not_spend_a_chat_turn(tmp_path: Path):
    """sage-chat writes an Artifact under examples/, never an app.

    Running the turn first spends up to the turn timeout and ends exactly where the offer starts —
    which is how "lets build the webapp" became 90 seconds of spinner and "ask again with a smaller
    question". The regex half needs no model call, so it can answer before a turn begins.
    """
    orch, oc = _orch(tmp_path, [Turn(text="should never run")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "lets build the webapp"))

    assert oc.prompts == []  # no turn was sent at all
    suggest = next(e for e in events if e["type"] == "handoff-suggest")
    assert suggest["reason"] == "explicit"
    done = next(e for e in events if e["type"] == "done")
    assert done["ok"] is True and done["decision"] == "handoff"
    # And it survives a reload, like any other turn event.
    assert any(e.get("type") == "handoff-suggest" for e in orch.thread_history(tid))


def test_declining_the_offer_leaves_chat_answering_build_words(tmp_path: Path):
    # Suppressed once means they chose Chat. A later build word must not short-circuit the turn.
    orch, oc = _orch(tmp_path, [Turn(text="Here is what that would take.")])
    tid = orch.create_thread()["id"]
    orch.patch_thread(tid, {"handoff": "suppress"})

    events = list(orch.chat_stream(tid, "lets build the webapp"))

    assert len(oc.prompts) == 1
    assert not any(e["type"] == "handoff-suggest" for e in events)


def test_a_build_request_the_regex_misses_still_offers_build_after_a_timeout(
    tmp_path: Path, monkeypatch
):
    """The model classifier judges the assistant's reply too, so it can only run after a turn — and
    a turn stopped at the timeout used to return before reaching it."""
    from sage.orchestrator import handoff as chat_handoff

    monkeypatch.setattr(chat_handoff, "wants_an_app", lambda **kw: True)
    orch, oc = _orch(tmp_path, [Turn(text="never emitted")])
    oc.stay_running = True
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(
        tid, "could this be something the team opens every morning?", timeout_s=0.05))

    suggest = next(e for e in events if e["type"] == "handoff-suggest")
    assert suggest["reason"] == "classifier"
    err = next(e for e in events if e["type"] == "error")
    assert "open it in Build" in err["message"]
    assert "smaller question" not in err["message"]   # wrong advice for a build request
    assert any(e.get("type") == "handoff-suggest" for e in orch.thread_history(tid))


def test_a_slow_question_that_is_not_a_build_keeps_the_narrower_query_advice(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="never emitted")])
    oc.stay_running = True
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "count the rows by event type", timeout_s=0.05))

    assert not any(e["type"] == "handoff-suggest" for e in events)
    err = next(e for e in events if e["type"] == "error")
    assert "narrower query" in err["message"]

    done = next(e for e in events if e["type"] == "done")
    assert done == {"type": "done", "ok": False, "decision": "timeout"}
    hist = orch.thread_history(tid)
    assert any(e.get("type") == "error" for e in hist)
    assert any(e.get("decision") == "timeout" for e in hist)


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
    assert 'get_dataset("dataset-autodoc-ds_missing").download_file("positions.csv"' in prompt
    assert "Do not search this git repo" in prompt


def test_a_dataset_file_whose_path_is_really_its_id_still_gets_the_dataset_route(tmp_path: Path):
    # The shape the Workbench actually sent, and the reason the test above never caught this: the
    # client recovered a missing path by stripping one prefix off `dsfile:<id>:<relPath>`, leaving
    # `<id>:<relPath>`. Every `if path:` downstream believed it, so the turn told the agent to read
    # a path that cannot exist instead of naming the Domino data library, and the answer never came.
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "file",
        "name": "price_data.csv",
        "path": "690d119f8a0ee66d0ee23533:price_data.csv",
        "datasetId": "690d119f8a0ee66d0ee23533",
        "datasetRelPath": "price_data.csv",
        "datasetName": "prices",
    })
    list(orch.chat_stream(tid, "whats in price_data.csv"))
    prompt = oc.prompts[0]["text"]
    assert "690d119f8a0ee66d0ee23533:price_data.csv" not in prompt
    assert 'download_file("price_data.csv"' in prompt


def test_new_conversation_does_not_provision(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    first = orch.create_thread()
    second = orch.create_thread()
    assert first["id"] != second["id"]
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
    # The digest is background only. `implement_note` puts the one framing line in front of it.
    assert "The plan is what to build" not in handoff_md
    assert "The plan is what to build" in handoff.implement_note(ws.path)
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
    # Not a build word: an explicit one is answered by the offer without spending a turn.
    list(orch.chat_stream(tid, "which desk is largest?"))
    with pytest.raises(ValueError, match="empty plan"):
        orch.draft_handoff_plan(tid)
    assert (orch.get_thread(tid)["handoff"] or {}).get("status") != "planned"


def test_default_slug_hydrates_the_default_chip(tmp_path: Path, monkeypatch):
    from sage.provision import naming

    monkeypatch.setenv("DOMINO_USER_NAME", "alice")
    monkeypatch.setenv("DOMINO_USER_ID", "507f1f77bcf86cd799439011")
    slug = naming.default_project_name("alice", "507f1f77bcf86cd799439011")
    orch, _ = _orch(tmp_path, project_id=slug)
    project = orch.project(start_preview=False)
    assert project.workspace.is_untitled() is True
    assert project.status()["untitled"] is True
    assert project.status()["name"] == "Default"  # the chip's word for the overlay (ADR-0004)


def test_named_project_does_not_hydrate_untitled(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert project.workspace.is_untitled() is False
    assert project.status()["name"] == "Sage"


# --- the live event stream -----------------------------------------------------------------------
# The turn loop reads the transcript once a second and shows nothing until the turn ends, so a
# question that takes ninety seconds is ninety seconds of spinner. These cover the other path: the
# answer arrives on OpenCode's /event stream while it is being written. The poll stays underneath —
# it is what ends the turn, and it is what runs when there is no stream to be had.

def _live(kind: str, **payload):
    from sage.driver.agent_driver import AgentEvent
    return AgentEvent(kind=kind, payload=payload)


class _FakeStream:
    """Stands in for SessionEvents: scripted frames, then it stays open the way /event does.

    `gap` spreads the frames over time the way a real turn arrives them. Instant delivery is fine
    for asking what a turn shows; it cannot ask how long a turn is allowed to take, because every
    frame lands in the first poll and the rest of the turn is silence either way.
    """

    def __init__(self, events, gap: float = 0.0):
        import threading
        self._events = list(events)
        self._gap = gap
        self.delivered = threading.Event()
        self._closed = threading.Event()

    def __iter__(self):
        for ev in self._events:
            if self._gap:
                # Waiting on `_closed` rather than sleeping so close() ends the turn's thread now.
                self._closed.wait(self._gap)
            if self._closed.is_set():
                break
            yield ev
        self.delivered.set()
        self._closed.wait(5)

    def close(self):
        self._closed.set()


class StreamingFake(FakeOpenCode):
    """A FakeOpenCode that can stream. `reads` records every transcript read, which is the thing
    the stream is supposed to make rare."""

    def __init__(self, workspace, turns, events, gap: float = 0.0):
        super().__init__(workspace, turns)
        self.stream = _FakeStream(events, gap)
        self.reads: list = []
        self.stream_dir = None
        self._grace = False

    def session_events(self, session_id, *, directory=None):
        self.stream_dir = directory
        return self.stream

    def messages(self, session_id, *, limit=None):
        self.reads.append(limit)
        return super().messages(session_id, limit=limit)

    def is_running(self, session_id):
        # True until every scripted frame is on the tap's queue, then one poll more: a real session
        # goes idle a moment after its last token, and the loop drains before it asks.
        # `stay_running` holds it there for good — a session whose tool never comes back.
        if self.stay_running or not self.stream.delivered.is_set():
            return True
        if not self._grace:
            self._grace = True
            return True
        return False


_A_TURN = [
    _live("message", delta="The ", final=False),
    _live("tool_run", tool="bash", input={"command": "python plot.py"}, call_id="c1", status="called"),
    _live("message", delta="answer.", final=False),
    _live("message", text="The answer.", final=True),
    _live("phase", finish="stop"),
]


def _streamed(tmp_path: Path, events=None, text: str = "The answer.", gap: float = 0.0):
    def client(ws):
        return StreamingFake(ws, [Turn(text=text)], _A_TURN if events is None else events, gap)
    return _orch(tmp_path, client=client)


def test_the_answer_arrives_while_the_turn_runs_instead_of_after_it(tmp_path: Path):
    orch, _ = _streamed(tmp_path)
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "what is it"))

    deltas = [e for e in out if e.get("type") == "delta"]
    assert [d["text"] for d in deltas] == ["The ", "answer.", "The answer."]
    # The last one is the whole text, not the last fragment: /event cannot be replayed, so a dropped
    # frame leaves the live copy short and this is the event that makes it whole again.
    assert deltas[-1]["final"] is True
    # And the spinner runs when the command starts rather than when it finishes.
    assert {"type": "agent", "kind": "tool", "tool": "bash",
            "doing": "bash", "detail": ""} in out


def test_streaming_leaves_the_record_of_the_turn_exactly_as_it_was(tmp_path: Path):
    """`delta` is the turn happening; the transcript is the record of it. If deltas reached
    history.jsonl a replayed Thread would show the answer once per fragment."""
    orch, _ = _streamed(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what is it"))

    history = orch.get_thread(tid)["history"]
    texts = [e for e in history if e.get("type") == "agent" and e.get("kind") == "text"]
    assert [e["text"] for e in texts] == ["The answer."]
    assert not [e for e in history if e.get("type") == "delta"]


def test_a_streamed_turn_reads_the_transcript_once_instead_of_once_a_second(tmp_path: Path):
    """The poll re-read the newest messages every second for the length of the turn, so the cost of
    asking grew with the length of the Thread rather than with the question — on the same box the
    agent was working on. With the stream up the transcript is read to open the turn and to close
    it, and not in between."""
    orch, oc = _streamed(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what is it"))

    assert [r for r in oc.reads if r is not None] == [20, 20]


def test_the_stream_is_asked_for_the_workspace_because_it_answers_per_directory(tmp_path: Path):
    """/event serves only the directory the connection asks for. Omit it and the subscriber gets
    the server's own working directory instead — a stream of heartbeats and nothing else, on a turn
    that is running perfectly well somewhere the subscriber is not listening. Measured against the
    pinned binary: 0 session frames without the parameter, every frame of the turn with it. Nothing
    fails when it is wrong; Chat just quietly polls forever."""
    orch, oc = _streamed(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what is it"))

    assert oc.stream_dir == str(orch.project(start_preview=False).workspace.path)


def test_a_driver_that_cannot_stream_polls_exactly_as_before(tmp_path: Path):
    """The stream is an improvement on the poll, never a replacement for it. A tap that reports
    itself not-ok on its first tick is what keeps the old path intact."""
    from sage.orchestrator.service import _EventTap

    class NoStream:
        pass

    assert _EventTap(NoStream(), "s").ok is False
    assert _EventTap(NoStream(), "s").drain() == []


def test_a_stream_that_delivers_nothing_still_answers(tmp_path: Path):
    """A provider that does not stream must cost the turn nothing at all: the transcript read at the
    end is what produces the answer, exactly as it did before any of this existed."""
    orch, _ = _streamed(tmp_path, events=[], text="Still answered.")
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "what is it"))

    assert not [e for e in out if e.get("type") == "delta"]
    assert {"type": "agent", "kind": "text", "text": "Still answered."} in out


def test_the_live_stream_carries_the_answer_and_the_work_and_nothing_else():
    from sage.orchestrator.service import _chat_live_event

    assert _chat_live_event(_live("message", delta="Bl", final=False)) == {"type": "delta", "text": "Bl"}
    assert _chat_live_event(_live("message", text="Blue.", final=True)) == {
        "type": "delta", "text": "Blue.", "final": True}
    # An empty fragment is not an event.
    assert _chat_live_event(_live("message", delta="", final=False)) is None
    # The shape the event catalogue advertises for shell.started, which never fired live. Read
    # anyway, because the frame that did fire was the one that looked less obvious.
    assert _chat_live_event(_live("tool_run", tool="bash", command="ls", status="called")) == {
        "type": "agent", "kind": "tool", "tool": "bash", "doing": "bash", "detail": ""}
    # A turn ends through is_running, never through the stream: finish="stop" is the model's stop
    # reason, and a step that ends on tool-calls has another step behind it.
    assert _chat_live_event(_live("phase", finish="stop")) is None


def test_chat_names_the_two_slow_things_that_arrive_as_bash():
    """Sage's own prompt tells the agent to reach a Data Source with `get_datasource(...)` and a
    Dataset file with `download_file(...)`, so both run as bash. They are also the two that take
    minutes. Naming the tool alone would have called the 5.5M-row query "Running Python…" — which
    is exactly what it said while the turn looked hung."""
    from sage.orchestrator.service import _chat_live_event

    query = _live("tool_run", tool="bash", status="called", input={"command":
        'python -c \'from domino_data.data_sources import DataSourceClient; '
        'DataSourceClient().get_datasource("BigQuery_Demo").query("SELECT 1")\''})
    assert _chat_live_event(query) == {"type": "agent", "kind": "tool", "tool": "bash",
                                       "doing": "query", "detail": "BigQuery_Demo"}

    dataset = _live("tool_run", tool="bash", status="called", input={"command":
        'python -c \'DatasetClient().get_dataset("prices").download_file("price_data.csv", '
        '"/tmp/price_data.csv")\''})
    assert _chat_live_event(dataset) == {"type": "agent", "kind": "tool", "tool": "bash",
                                         "doing": "read", "detail": "price_data.csv"}

    # Everything else bash does is still just Python running.
    plain = _live("tool_run", tool="bash", status="called", input={"command": "python plot.py"})
    assert _chat_live_event(plain) == {"type": "agent", "kind": "tool", "tool": "bash",
                                       "doing": "bash", "detail": ""}


def test_a_local_file_is_named_by_the_tool_that_touches_it():
    from sage.orchestrator.service import _chat_live_event

    read = _live("tool_run", tool="read", status="called", input={"filePath": "examples/a.csv"})
    assert _chat_live_event(read) == {"type": "agent", "kind": "tool", "tool": "read",
                                      "doing": "read", "detail": "examples/a.csv"}
    write = _live("tool_run", tool="write", status="called", input={"path": "examples/x.png"})
    assert _chat_live_event(write) == {"type": "agent", "kind": "tool", "tool": "write",
                                       "doing": "write", "detail": "examples/x.png"}


def test_a_label_stops_being_true_the_moment_the_work_stops():
    """A label that outlives its work names the wrong thing and never moves, which is the thing
    that reads as a hang. Both a finished call and a call Chat does not name clear it."""
    from sage.orchestrator.service import _chat_live_event

    idle = {"type": "agent", "kind": "tool", "doing": "idle"}
    # The completion carries no tool name at all — measured — so it can only mean "stop saying it".
    assert _chat_live_event(_live("tool_run", tool="", call_id="c1", status="success")) == idle
    assert _chat_live_event(_live("tool_run", tool="bash", call_id="c1", status="failed")) == idle
    # grep and glob are not worth naming, but they still end whatever the last label said.
    assert _chat_live_event(_live("tool_run", tool="grep", status="called",
                                  input={"pattern": "x"})) == idle


# --- The turn cap is quiet time, not wall clock -------------------------------------------------

_A_LONG_TURN = [
    _live("message", delta="Looking. ", final=False),
    _live("tool_run", tool="bash", input={"command": "get_datasource('WH')"}, status="called"),
    _live("tool_run", tool="", call_id="c1", status="success"),
    _live("message", delta="Charting. ", final=False),
    _live("tool_run", tool="write", input={"path": "examples/x.png"}, status="called"),
    _live("message", text="Done.", final=True),
    _live("phase", finish="stop"),
]


def test_a_turn_that_keeps_working_outlives_the_quiet_window(tmp_path: Path):
    """The wall clock could not tell a slow turn from a stuck one, so it killed both. This turn
    runs for longer than the whole window and finishes, because it never stops saying so."""
    orch, oc = _streamed(tmp_path, _A_LONG_TURN, text="Done.", gap=0.25)
    tid = orch.create_thread()["id"]
    started = time.monotonic()
    out = list(orch.chat_stream(tid, "chart last quarter", timeout_s=0.6))
    elapsed = time.monotonic() - started

    assert elapsed > 0.6, "the turn ended too early to have outlived one quiet window"
    assert not [e for e in out if e["type"] == "error"]
    assert next(e for e in out if e["type"] == "done")["ok"] is True
    assert oc.interrupted == 0


def test_a_tool_that_never_comes_back_still_ends_the_turn(tmp_path: Path):
    """The live hang: the query starts, the session stays running, and nothing else arrives. A
    tool that is merely in flight is not activity, or the cap would never fire on the one case it
    exists for."""
    started_only = [
        _live("tool_run", tool="bash", input={"command": "get_datasource('WH')"}, status="called"),
    ]
    orch, oc = _streamed(tmp_path, started_only, gap=0.1)
    oc.stay_running = True
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "how many rows", timeout_s=0.5))

    assert oc.interrupted == 1
    # The label it died under is the one that names what to do about it.
    assert {"type": "agent", "kind": "tool", "tool": "bash",
            "doing": "query", "detail": "WH"} in out
    assert "narrower query" in next(e for e in out if e["type"] == "error")["message"]


def test_a_turn_that_never_stops_talking_hits_the_ceiling(tmp_path: Path, monkeypatch):
    """Alive is not the same as getting anywhere. Without a ceiling an agent that loops holds the
    turn lock for as long as it keeps emitting."""
    from sage.orchestrator import service

    forever = [_live("message", delta="and ", final=False) for _ in range(40)]
    monkeypatch.setattr(service, "_CHAT_TURN_MAX_S", 0.8)
    orch, oc = _streamed(tmp_path, forever, gap=0.05)
    oc.stay_running = True
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "keep going"))

    assert oc.interrupted == 1
    err = next(e for e in out if e["type"] == "error")
    assert "worked for too long" in err["message"]
    assert "stopped making progress" not in err["message"]  # it never stopped; that is the point
    assert next(e for e in out if e["type"] == "done") == {
        "type": "done", "ok": False, "decision": "timeout"}


# --- Tidying up is not the turn ---


def test_the_lock_is_free_before_the_turn_finishes_tidying_up(tmp_path: Path):
    """`done` ends the turn; classify, compact and push are aftercare that runs after it.

    All three used to run with the turn lock still held, so the next question — typed the moment
    the answer appeared, which is when people type — was refused as busy until the tidying ended.
    """
    orch, _ = _orch(tmp_path, [Turn(text="Rates is the largest desk.")])
    tid = orch.create_thread()["id"]
    while_classifying: list[bool] = []

    def spy(*_a, **_k):
        while_classifying.append(orch.turn_busy())

    orch._maybe_suggest_handoff = spy
    at_done = None
    for ev in orch.chat_stream(tid, "what's our gross exposure by desk?"):
        if ev["type"] == "done":
            at_done = orch.turn_busy()

    assert at_done is False
    # The classifier is the slow one — up to handoff.TIMEOUT_S of gateway call, every turn that
    # has not been offered Build yet — and it needs nothing the next turn needs.
    assert while_classifying == [False]


def test_a_commit_waits_for_the_turn_that_beat_it_to_the_lock(tmp_path: Path):
    """The save runs off the turn lock now, so it has to take it back: it commits the whole tree,
    pulls, and can run the conflict turn. Losing that race defers the commit; it never drops it."""
    orch, _ = _orch(tmp_path, [Turn(text="Rates.")])
    calls = _track_saves(orch)
    tid = orch.create_thread()["id"]

    def next_turn_gets_there_first(*_a, **_k):
        orch._turn_lock.acquire()

    orch._maybe_suggest_handoff = next_turn_gets_there_first
    try:
        events = list(orch.chat_stream(tid, "what's our gross exposure by desk?"))
    finally:
        orch._turn_lock.release()

    assert calls == []
    assert not any(e["type"] == "saved" for e in events)
    assert orch._chat_dirty is True
    assert orch._chat_save_timer is not None
    orch._cancel_chat_idle_save()
