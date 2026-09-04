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
    project = orch.project(start_preview=False)
    # At the Project root, where Chat's Threads and Artifacts live — not inside the Built App.
    work = project.record.path / ".sage" / "chat-work"
    assert oc.sessions[0]["directory"] == str(work)
    assert not str(work).startswith(str(project.workspace.path))
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


def test_chat_turn_records_artifact_and_reverts_a_write_outside_its_thread(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    thread = orch.create_thread()
    tid = thread["id"]
    table = '{"title": "Desks", "columns": ["desk"], "rows": [["Rates"]]}'
    oc.turns = [Turn(
        text="Here is gross notional by desk.",
        writes={
            "examples/notes.md": "# not this Thread's",
            f"examples/{tid}/exposure.table.json": table,
        },
    )]
    project = orch.project(start_preview=False)
    src = project.workspace.path / "src" / "App.tsx"
    original = src.read_text()
    events = list(orch.chat_stream(tid, "what's in this CSV?"))

    assert src.read_text() == original  # the app is a directory Chat cannot reach at all
    assert not (project.record.path / "examples" / "notes.md").exists()  # reverted
    arts = next(e for e in events if e.get("type") == "artifacts")["items"]
    assert arts[0]["kind"] == "table"
    assert arts[0]["path"] == f"examples/{tid}/exposure.table.json"
    assert (project.record.path / arts[0]["path"]).read_text() == table
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


def test_chat_prompt_builds_the_dataset_handle_from_the_resource_id(tmp_path: Path):
    """The client posts the Domino id as `resourceId` and no `id` at all (`api.js`
    `addToConversation`), so `add_context` mints the row a `ctx_` id of its own. Reading `id`
    here therefore built `dataset-<name>-ctx_...` and Domino answered, correctly, "Cannot find
    Dataset entry." The test above passes an explicit `id`, which the `**item` spread keeps —
    a shape that only a catalogue row has, which is why it never saw this.
    """
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    stored = orch.add_thread_context(
        tid,
        {"kind": "dataset", "name": "autodoc", "project": "Sage",
         "resourceId": "dataset:abc123", "addedBy": "user"},
    )
    # The row really does carry a local id, so the assert below is about which field is read.
    assert stored["id"].startswith("ctx_")
    list(orch.chat_stream(tid, "whats in autodoc"))
    prompt = oc.prompts[0]["text"]
    assert 'get_dataset("dataset-autodoc-abc123")' in prompt
    assert "ctx_" not in prompt


def test_chat_prompt_says_so_when_a_dataset_chip_has_no_identifier(tmp_path: Path):
    """No `resourceId` either, so the only id the row has is the `ctx_` one `add_context`
    minted. There is no handle to build, and the honest sentence already written for that is
    worth more than a call that cannot succeed: it is reachable only once a `ctx_` id is
    refused. Held here rather than in the pack tests because those call `_chat_context_line`
    directly, and it is the route through `add_thread_context` that had no cover.
    """
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "dataset", "name": "autodoc", "project": "Sage"})
    list(orch.chat_stream(tid, "whats in autodoc"))
    prompt = oc.prompts[0]["text"]
    assert "has no identifier for it" in prompt
    assert "get_dataset(" not in prompt


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
@pytest.mark.parametrize("prompt", [
    "lets build the webapp",
    # Live, and the third phrasing of the same request to reach the classifier instead of the
    # regex: no "me", no "web", so it spent the tool-quiet window on a sample it could not use.
    "lets build an app from a sample of 100 rows",
    # Live, and the fourth: "build ... into an app" reads as intent to every human and matched no
    # branch, because the into-an-app branch only knew "turn" and "convert".
    "ok now lets build this into an app that i can share",
    # The same sentence about the same thing under its other names. "app" alone made the branch
    # answer one phrasing of one request and spend a whole turn on the rest.
    "build this into a dashboard",
    "make this into a tool the team can use",
])
def test_an_explicit_build_request_does_not_spend_a_chat_turn(tmp_path: Path, prompt: str):
    """sage-chat writes an Artifact under examples/, never an app.

    Running the turn first spends up to the turn timeout and ends exactly where the offer starts —
    which is how "lets build the webapp" became 90 seconds of spinner and "ask again with a smaller
    question". The regex half needs no model call, so it can answer before a turn begins.
    """
    orch, oc = _orch(tmp_path, [Turn(text="should never run")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, prompt))

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


def test_a_timed_out_turn_still_records_what_it_had_already_written(tmp_path: Path):
    """A turn that runs out of time has usually written something first, and those files are all
    the person has to show for the wait. Only the path that reaches the end of the turn recorded
    them, so a timed-out one left them on disk and unlisted — and `examples/` crosses into Build
    by that list (handoff.md §1), which made the Build offer on the very same message an offer to
    start again with none of the work."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    table = '{"title": "Sample", "columns": ["desk"], "rows": [["Rates"]]}'
    oc.turns = [Turn(text="never emitted",
                     writes={f"examples/{tid}/sample.table.json": table})]
    oc.stay_running = True

    events = list(orch.chat_stream(tid, "sample 100 rows of the clickstream", timeout_s=0.05))

    arts = next(e for e in events if e.get("type") == "artifacts")["items"]
    assert arts[0]["path"] == f"examples/{tid}/sample.table.json"
    assert arts[0]["kind"] == "table"
    # What the turn produced comes before the reason it stopped, as on the path that finishes.
    kinds = [e["type"] for e in events]
    assert kinds.index("artifacts") < kinds.index("error")
    done = next(e for e in events if e["type"] == "done")
    assert done["decision"] == "timeout" and done["artifacts"] == arts
    # And they are the Thread's afterwards, not just this stream's.
    assert any(e.get("type") == "artifacts" for e in orch.thread_history(tid))
    assert orch.get_thread(tid)["artifacts"][0]["path"] == arts[0]["path"]


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


def test_a_dataset_file_chip_is_fetched_for_the_question_not_for_the_app(tmp_path: Path):
    """The chip used to go through attach_file, so asking about a file wrote it into the published
    app's asset tree and its committed manifest. A question has no app to serve it to."""
    orch, oc = _orch(tmp_path, [Turn(text="Monthly revenue.")])
    ws = orch.project(start_preview=False).workspace.path
    ds = next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026")
    tid = orch.create_thread()["id"]

    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "train.csv", "datasetName": "sales_2026",
        "datasetId": ds, "datasetRelPath": "train.csv",
    })
    list(orch.chat_stream(tid, "whats in @train.csv"))

    assert row["path"] == ".sage/scratch/datasets/sales_2026/train.csv"
    # The app's data tree is linked into the chat workdir, so it exists — but nothing was put in it.
    assert list((ws / "public" / "data").iterdir()) == []
    assert "public/data/" in (ws / ".gitignore").read_text()
    assert row["path"] in oc.prompts[0]["text"]
    # And the path in the prompt resolves where the agent actually stands.
    work = Path(oc.sessions[0]["directory"])
    assert (work / row["path"]).read_text().startswith("month,revenue")


def test_a_confirmed_handoff_puts_the_chat_file_where_the_app_reads_it(tmp_path: Path):
    """The handoff is the moment the Thread becomes an app, so it is the moment the bytes belong
    in `public/data/` and in the manifest a publish rehydrates from."""
    orch, _ = _orch(tmp_path, [Turn(text="Monthly revenue."), Turn(text=_PLAN)])
    ds = next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026")
    tid = orch.create_thread()["id"]
    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "train.csv", "datasetName": "sales_2026",
        "datasetId": ds, "datasetRelPath": "train.csv",
    })
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)

    orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})

    # Read after the confirm: that is when the app exists, and a confirm makes a NEW one, so an app
    # named before it is not the one the file belongs in (ADR-0008, #69).
    ws = orch.project(start_preview=False).workspace.path
    served = ws / "public" / "data" / "sales_2026" / "train.csv"
    assert served.read_text().startswith("month,revenue")
    manifest = json.loads((ws / ".sage" / "attachments.json").read_text())
    assert [e["path"] for e in manifest] == ["public/data/sales_2026/train.csv"]
    # The Thread goes on working: its chip still names a file that is there. Chat's scratch is the
    # Project's, so the chip's path is read from the root and not from the app.
    assert (orch.project(start_preview=False).record.path / row["path"]).exists()


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


def test_a_bound_thread_is_offered_a_second_handoff(tmp_path: Path):
    """Handoff spec §8, criterion 10. The conversation that produced an app keeps going, and what
    it asks for next may be another app — the record is a list, so the entry that bound does not
    close the Thread to a second suggestion (#72)."""
    gw = ScriptedGateway("APP")
    orch, _oc = _orch(tmp_path, [
        Turn(text="A dashboard, then."),
        Turn(text=_PLAN),
        Turn(text="A report, then."),
    ], gateway=gw)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, {"resources": False, "artifacts": False, "transcript": False})
    assert orch.get_thread(tid)["handoff"]["status"] == "bound"

    later = list(orch.chat_stream(tid, "we also need a daily P&L report for the desk heads"))

    assert any(e.get("type") == "handoff-suggest" for e in later)
    thread = orch.get_thread(tid)
    assert thread["handoff"]["status"] == "suggested"
    assert thread["handoff"]["suppressed"] is False
    # The offer is about a second app, so it has no plan yet — and the Thread still carries the
    # plan document of the app it already built.
    assert "planId" not in thread["handoff"]
    assert thread["planId"] == "001"


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
    # The plan lives in the document, which is the Project's. `.sage/plan.md` and `.sage/handoff.md`
    # are the BUILDER's copies and the builder has no app yet — the confirm writes them (ADR-0008).
    project = orch.project(start_preview=False)
    assert project.record.read_plan_doc("001")["markdown"].startswith("A desk exposure dashboard.")
    assert project.workspace.read_plan() is None
    assert not (project.workspace.path / ".sage" / "handoff.md").exists()
    assert project.workspace.read_history() == []
    # And it planned in the Thread's own session, not in a Build session opened on a missing app.
    from sage.workspace.threads import ThreadStore
    assert oc.prompts[-1]["session"] == ThreadStore(project.record.path).read_session(tid)["session_id"]

    again = orch.draft_handoff_plan(tid)
    assert again["handoff"]["status"] == "planned"
    assert again["plan"].startswith("A desk exposure dashboard.")   # re-read from the document
    assert sum(1 for p in oc.prompts if p["agent"] == "sage-plan") == 1


def test_the_handoff_plan_turn_asks_for_the_plan_document_shape(tmp_path: Path):
    """This turn writes a plan document, and the document is parsed out of these headings.

    Live, asking only for "a concrete build plan" got narration back — "I'm turning that background
    work into a concrete app brief…" — and prose has no headings to parse, so the plan page showed a
    title over eight empty sections while the transcript held the plan in full."""
    orch, oc = _orch(tmp_path, [
        Turn(text="Rates is the largest desk."),
        Turn(text=_PLAN),
    ])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)

    sent = oc.prompts[-1]["text"]
    assert "## Problem & outcome" in sent
    assert "## Screens" in sent
    assert "## Done when" in sent
    assert "future tense" in sent          # and in the voice the approval card is read in


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
    orch.project(start_preview=False).record.mark_untitled(True)

    result = orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})
    assert result["ok"] is True
    assert result["handoff"]["status"] == "bound"
    project = orch.project(start_preview=False)
    ws = project.workspace
    handoff_md = (ws.path / ".sage" / "handoff.md").read_text()
    assert "trades" in handoff_md
    # The digest is background only. `implement_note` puts the one framing line in front of it.
    assert "The plan is what to build" not in handoff_md
    assert "The plan is what to build" in handoff.implement_note(ws.path)
    assert not (ws.path / ".sage" / "handoff-transcript.md").exists()
    bindings = ws.read_bindings()
    assert any(b.get("id") == "ds-trades" for b in bindings)
    assert src.read_text() == before
    # The plan title names the APP, and the Project keeps the name it had. Confirming used to
    # rename the Project to the plan title, which was a Project-per-app rule: a Project holds many
    # apps now, and two of them cannot share one name (ADR-0008, #73).
    assert result["title"] == "A desk exposure dashboard."
    assert project.record.is_untitled() is True
    assert project.record.display_name() == "Default"
    assert ws.display_name() == "A desk exposure dashboard."

    orch.confirm_handoff(tid, {"transcript": True})
    assert (ws.path / ".sage" / "handoff-transcript.md").exists()


def test_confirm_handoff_binds_the_data_source_a_table_chip_came_from(tmp_path: Path):
    """A table chip carries the TABLE's name. The Binding has to carry the SOURCE's.

    `Binding.name` is what the published app passes to `get_datasource` (template/serve.py), so a
    handoff that recorded the chip's own name shipped an app that could not open its own store —
    live, "This app could not open the Data Source it reads." Going through `bind_data_source`
    resolves the name off the live listing, and brings the connector type with it.
    """
    orch, _ = _orch(tmp_path, [Turn(text="Rates."), Turn(text=_PLAN)])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "DIM_ACCOUNT",
        "bindingKey": ["data_source", "ds-dwh"],
        "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})

    ws = orch.project(start_preview=False).workspace
    bound = next(b for b in ws.read_bindings() if b.get("id") == "ds-dwh")
    assert bound["name"] == "Snowflake-Data-Warehouse"
    assert bound["display_name"] == "Snowflake-Data-Warehouse"
    assert bound["connector_type"] == "SnowflakeConfig"
    assert bound["table"] == "DIM_ACCOUNT"
    # bind_data_source also reads the columns the build agent writes SQL from; _record never did.
    assert (ws.path / ".sage" / "schema.json").exists()


def test_confirm_handoff_records_a_data_source_the_listing_no_longer_has(tmp_path: Path):
    """One renamed or revoked source must not cost the whole handoff. It is recorded unresolved."""
    orch, _ = _orch(tmp_path, [Turn(text="Rates."), Turn(text=_PLAN)])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "trades",
        "bindingKey": ["data_source", "ds-gone"],
    })
    list(orch.chat_stream(tid, "put this on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)
    result = orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})

    assert result["ok"] is True
    ws = orch.project(start_preview=False).workspace
    assert any(b.get("id") == "ds-gone" for b in ws.read_bindings())


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
    assert project.record.is_untitled() is True
    assert project.status()["untitled"] is True
    assert project.status()["name"] == "Default"  # the chip's word for the overlay (ADR-0004)


def test_named_project_does_not_hydrate_untitled(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert project.record.is_untitled() is False
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


def test_the_stream_is_asked_for_the_session_directory_because_it_answers_per_directory(tmp_path: Path):
    """/event serves only the directory the connection asks for. Ask for the wrong one and the
    subscriber gets another project's events — a stream of heartbeats and nothing else, on a turn
    that is running perfectly well somewhere the subscriber is not listening. Measured against the
    pinned binary: 0 session frames without the parameter, every frame of the turn with it.

    The directory that matters is the SESSION's. This asked for the workspace root, which is right
    for Build and wrong for Chat: a Chat session is created in `.sage/chat-work`. Nothing failed
    when it was wrong — the socket opened and stayed open — so Chat ran every turn blind to its own
    events and killed anything longer than the quiet window."""
    orch, oc = _streamed(tmp_path)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what is it"))

    assert oc.stream_dir == oc.sessions[0]["directory"]
    assert oc.stream_dir.endswith(".sage/chat-work")


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


def test_a_chat_answer_never_shows_the_build_marker(tmp_path: Path):
    """A Chat session's directory sits inside the app repo, so the chat agent is handed the app's
    AGENTS.md as well as its own — NOTHING_TO_BUILD included, and a Chat turn earns that line by
    definition. It is a signal to Sage, so someone who asked how many rows a file has reads the
    answer and not a code under it (the defect this test is named for)."""
    from sage.orchestrator.service import _chat_live_event

    orch, _ = _orch(tmp_path, [Turn(text="The file has 220 rows of adverse events.\nNOTHING_TO_BUILD")])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "how many rows does @synthetic_adverse_events.csv have?"))
    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    assert [t.strip() for t in texts] == ["The file has 220 rows of adverse events."]
    # And not on reload either: the transcript event is the only copy the server keeps.
    assert "NOTHING_TO_BUILD" not in json.dumps(orch.thread_history(tid))
    # The final frame is what repairs the streamed copy, so it carries the stripped text too.
    assert _chat_live_event(_live("message", text="220 rows.\nNOTHING_TO_BUILD\n", final=True)) == {
        "type": "delta", "text": "220 rows.\n", "final": True}


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


def test_a_slow_tool_outlives_the_window_that_ends_a_stalled_model(tmp_path: Path, monkeypatch):
    """`download_file` on a real Dataset file is silent for as long as it takes. One window could
    not tell that from a stalled model, so it killed the one thing the turn was told to do — and
    then told the person their query was too broad."""
    from sage.orchestrator import service
    monkeypatch.setattr(service, "_CHAT_QUIET_TIMEOUT_S", 0.1)
    monkeypatch.setattr(service, "_CHAT_TOOL_QUIET_TIMEOUT_S", 1.0)
    downloading = [
        _live("tool_run", tool="bash", call_id="c1", status="called",
              input={"command": 'DatasetClient().get_dataset("dataset-clickstream-ds1")'
                                '.download_file("clean_cc_transactions.csv", "/tmp/x.csv")'}),
    ]
    orch, oc = _streamed(tmp_path, downloading, gap=0.05)
    oc.stay_running = True
    tid = orch.create_thread()["id"]

    started = time.monotonic()
    out = list(orch.chat_stream(tid, "whats in @clean_cc_transactions.csv"))
    elapsed = time.monotonic() - started

    assert elapsed > 0.5, "the download died on the window it is not subject to"
    assert oc.interrupted == 1
    err = next(e for e in out if e["type"] == "error")
    # It did not stop working, so it must not be reported as having stopped.
    assert "stopped making progress" not in err["message"]
    assert "The step Sage was running did not finish" in err["message"]
    assert "narrower query" in err["message"]


def test_a_finished_tool_puts_the_turn_back_on_the_short_window(tmp_path: Path, monkeypatch):
    """The longer window belongs to the tool, not to the rest of the turn. A model that stalls
    after its query came back is a stalled model, and it is named as one."""
    from sage.orchestrator import service
    monkeypatch.setattr(service, "_CHAT_QUIET_TIMEOUT_S", 0.4)
    monkeypatch.setattr(service, "_CHAT_TOOL_QUIET_TIMEOUT_S", 30.0)
    came_back = [
        _live("tool_run", tool="bash", call_id="c1", status="called",
              input={"command": "get_datasource('WH')"}),
        _live("tool_run", tool="", call_id="c1", status="success"),
    ]
    orch, oc = _streamed(tmp_path, came_back, gap=0.05)
    oc.stay_running = True
    tid = orch.create_thread()["id"]

    out = list(orch.chat_stream(tid, "how many rows"))

    assert oc.interrupted == 1
    assert "stopped making progress" in next(e for e in out if e["type"] == "error")["message"]


def test_a_stream_that_says_nothing_does_not_blind_the_turn(tmp_path: Path):
    """`ok` says the socket is up, not that it carries anything. A tap that connects onto silence
    kept the turn on the fast path — the transcript read once at the end, so nothing moved the
    clock and nothing tracked a tool — and every turn longer than the quiet window then died
    reporting that Sage had stopped making progress, while the agent was still working.

    The mechanism is the read: a silent stream must put the turn back on the transcript WHILE it
    runs, which is what the failed-tap fallback has always done."""
    class Silent(StreamingFake):
        def __init__(self, ws, turns):
            super().__init__(ws, turns, [], 0.0)
            self.polls = 0

        def is_running(self, session_id):
            self.polls += 1
            return self.polls < 4

    orch, oc = _orch(tmp_path, client=lambda ws: Silent(ws, [Turn(text="The answer.")]))
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "what is it"))

    # Baseline plus one read per running poll, not baseline plus the single read at the end.
    assert len([r for r in oc.reads if r == 20]) > 2
    assert [e["text"] for e in out if e.get("kind") == "text"] == ["The answer."]
    assert not [e for e in out if e["type"] == "error"]


def test_a_refused_step_says_what_was_refused(tmp_path: Path):
    """A step the provider says no to ends the turn with no answer in it. Chat dropped that frame,
    waited out the quiet cap and reported that Sage "stopped making progress" — which sent the
    person to shrink a question that was never what went wrong."""
    refused = [_live("error", error={"data": {"message": "context length exceeded"}})]

    def client(ws):
        return StreamingFake(ws, [Turn(text="")], refused, 0.0)

    orch, _ = _orch(tmp_path, client=client)
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "whats in @transformed_cc_transactions.csv"))

    err = next(e for e in out if e["type"] == "error")
    assert "context length exceeded" in err["message"]
    assert "stopped making progress" not in err["message"]
    assert next(e for e in out if e["type"] == "done") == {
        "type": "done", "ok": False, "decision": "step failed"}
    # And the Thread keeps it, so a reload still shows why.
    assert any(e.get("type") == "error" for e in orch.get_thread(tid)["history"])


def test_a_refused_step_the_turn_recovers_from_is_not_reported(tmp_path: Path):
    """One step failing is not the turn failing. If an answer arrives after it, the answer is the
    turn — the frame was a retry, and saying anything about it would be noise."""
    recovered = [
        _live("error", error={"name": "ProviderError"}),
        _live("message", text="It has 12 columns.", final=True),
        _live("phase", finish="stop"),
    ]
    orch, _ = _streamed(tmp_path, recovered, text="It has 12 columns.")
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "whats in it"))

    assert not [e for e in out if e["type"] == "error"]
    assert next(e for e in out if e["type"] == "done")["ok"] is True


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


def test_stopping_a_chat_turn_says_so_and_keeps_what_it_wrote(tmp_path: Path):
    """Chat had no Stop. The ten-minute ceiling on a turn was chosen as "generous, because by then
    the person can press Stop" — true of Build, and of nothing in Chat, so a wedged question held
    the project until it timed out and the next one was refused as busy.

    Stopping is not reverting: a Build stop takes the turn's file changes with it because half an
    app is worse than none, and a Chat turn's chart under examples/ is an answer that still reads.
    """
    orch, oc = _orch(tmp_path, [Turn(text="never emitted")])
    oc.stay_running = True
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.stop_requested = True   # a Stop that landed while the turn was in flight

    events = list(orch.chat_stream(tid, "how many rows does clickstream have?"))

    assert oc.interrupted == 1
    stopped = next(e for e in events if e["type"] == "stopped")
    assert "kept" in stopped["message"]
    done = next(e for e in events if e["type"] == "done")
    assert done["ok"] is False and done["decision"] == "stopped"
    # And it survives a reload — a stopped turn that leaves no trace is a question with no reply.
    history = orch.thread_history(tid)
    assert any(e.get("type") == "stopped" for e in history)


def test_a_stopped_chat_turn_does_not_take_the_next_question_with_it(tmp_path: Path):
    """`stop_requested` is a request, and the turn that consumes it has to clear it. Build clears it
    in handle_stop; Chat only ever read it, so the flag outlived the turn and the NEXT question died
    at the top of the poll loop before it ran a step — a Stop that silently ate the question after
    the one it was pressed for."""
    orch, oc = _orch(tmp_path, [Turn(text="never emitted"), Turn(text="Six million rows.")])
    oc.stay_running = True
    tid = orch.create_thread()["id"]
    orch.project(start_preview=False).stop_requested = True
    list(orch.chat_stream(tid, "how many rows does clickstream have?"))
    assert orch.project(start_preview=False).stop_requested is False

    oc.stay_running = False
    events = list(orch.chat_stream(tid, "ask it again"))
    assert any(e.get("type") == "agent" and e.get("text") == "Six million rows." for e in events)


def test_stop_with_nothing_running_is_not_a_trap_for_the_next_turn(tmp_path: Path):
    """Stop pressed twice, or pressed in the second a turn was already ending, used to leave the
    flag set with no turn to consume it. The next question then stopped itself before it ran. The
    turn lock is the honest test of "there is something to interrupt"."""
    orch, oc = _orch(tmp_path, [Turn(text="Six million rows.")])
    tid = orch.create_thread()["id"]

    orch.stop_build()

    assert oc.interrupted == 0   # there was no session to interrupt, and none was
    assert orch.project(start_preview=False).stop_requested is False
    events = list(orch.chat_stream(tid, "how many rows does clickstream have?"))
    assert any(e.get("type") == "agent" and e.get("text") == "Six million rows." for e in events)
