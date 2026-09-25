"""#557 P4: use the real Workbench store and its existing header harness."""
import json
import subprocess
from pathlib import Path

import pytest

from sage.workspace.threads import ThreadStore

HARNESS = Path(__file__).parent / "js" / "build_header_harness.mjs"


def run(step):
    result = subprocess.run(["node", str(HARNESS)], input=json.dumps([step]),
                            text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])[-1]


@pytest.mark.parametrize("race", ["switch", "return", "same", "project", "error", "thread"])
def test_old_history_cannot_replace_the_current_transcript(race):
    result = run({"historyRace": race})
    assert result["before"]["text"] == ["newest history"]
    assert result["after"] == result["before"]


def test_failed_refresh_keeps_history_and_retry_clears_the_error():
    result = run({"historyFailure": True})
    assert result["failed"]["text"] == ["saved answer"]
    assert result["failed"]["error"]
    assert "Retry" in result["failed"]["words"]
    assert "Built from a previous plan" not in result["failed"]["words"]
    assert result["after"] == ["retried answer"]
    assert not result["error"]


def test_last_used_conversation_is_per_project_and_deleted_preference_falls_back():
    result = run({"historyPreference": True})
    assert result == {"remembered": "thr_one", "otherProject": "thr_many", "deleted": "thr_many"}


def test_attempt_is_separate_from_a_change_receipt(tmp_path):
    store = ThreadStore(tmp_path)
    tid = store.create()["id"]
    store.record_attempt(tid, app_id="app_failed", app_name="Draft app")
    row = store.get(tid)
    assert row["touched"] == []
    assert row["attempted"][0]["appId"] == "app_failed"
    assert row["attempted"][0]["kind"] == "attempted"
    store.record_attempt(tid, app_id="app_failed", app_name="Draft app")
    assert len(store.get(tid)["attempted"]) == 1


def test_legacy_failed_history_is_indexed_once_without_rewriting_it(tmp_path, monkeypatch):
    store = ThreadStore(tmp_path)
    tid = store.create()["id"]
    meta = json.loads(store.meta_path(tid).read_text())
    meta.pop("attemptIndexVersion", None)
    store.meta_path(tid).write_text(json.dumps(meta))
    history = tmp_path / "apps" / "app_failed" / ".sage" / "history.jsonl"
    history.parent.mkdir(parents=True)
    body = json.dumps({"type": "done", "ok": False, "app": "app_failed", "conversation": tid}) + "\n"
    history.write_text(body)
    store.backfill_attempts({"app_failed": ("Draft app", history)})
    assert store.get(tid)["attempted"][0]["appId"] == "app_failed"
    assert history.read_text() == body
    original_open = Path.open

    def no_history_scan(path, *args, **kwargs):
        assert path != history, "an indexed conversation must not scan the app log again"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_history_scan)
    store.backfill_attempts({"app_failed": ("Draft app", history)})
    assert store.get(tid)["attempted"][0]["appId"] == "app_failed"


def test_delayed_artifact_conversion_cannot_replace_the_selected_conversation():
    result = run({"historyConversion": True})
    assert result == {"text": ["current answer"], "chat": 0, "error": None}


def test_switching_targets_hides_the_previous_app_and_restores_its_cache():
    assert run({"historyCache": True}) == {
        "emptyOnSwitch": 0, "cached": ["app_a/thr_one"]}


@pytest.mark.parametrize("caller", ["watcher", "recall"])
def test_background_and_recall_reads_use_the_same_target_guard(caller):
    assert run({"historyRefreshRace": caller})["text"] == ["current answer"]


def test_attempt_only_conversation_is_selectable_and_visible_in_the_filtered_rail():
    result = run({"historyAttempt": True})
    assert result["id"] == "thr_failed"
    assert "Failed plan" in str(result["rail"])
    assert "Attempted Rate curve viewer" in str(result["titles"])
    assert "Attempted Rate curve viewer" in str(result["labels"])


def test_attempt_labels_follow_app_rename_and_delete(tmp_path):
    store = ThreadStore(tmp_path)
    tid = store.create()["id"]
    store.record_attempt(tid, app_id="app_a", app_name="Draft")
    store.rename_app("app_a", "Named")
    assert store.get(tid)["attempted"][0]["appName"] == "Named"
    store.forget_app("app_a")
    assert store.get(tid)["attempted"] == []


def test_backfill_recovers_session_only_attempt_and_ignores_deleted_threads(tmp_path):
    store = ThreadStore(tmp_path)
    tid = store.create()["id"]
    gone = store.create()["id"]
    for thread in (tid, gone):
        meta = json.loads(store.meta_path(thread).read_text())
        meta.pop("attemptIndexVersion")
        store.meta_path(thread).write_text(json.dumps(meta))
        (store.thread_dir(thread) / "build-session-app_a.json").write_text(
            json.dumps({"session_id": "saved-session"}))
    store.delete(gone)
    store.backfill_attempts({"app_a": ("Draft", tmp_path / "absent-history")})
    assert store.get(tid)["attempted"][0]["appId"] == "app_a"
    assert store.get(gone) is None


@pytest.mark.parametrize("action", ["recall", "withhold"])
def test_failed_mutation_does_not_leave_a_history_spinner(action):
    assert run({"historyMutationError": action}) == {"loading": False}


def test_only_a_current_explicit_app_open_updates_return_choice_and_new_stays_new():
    assert run({"historyExplicitChoice": True}) == {
        "afterChat": "thr_one", "afterStale": "thr_one", "pending": True, "thread": None}


def test_admitted_failed_plan_records_app_without_claiming_a_change(tmp_path, monkeypatch):
    import time

    from .fake_opencode import Turn
    from .test_a_dropped_mention_reaches_the_agents_prompt import _orch

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(error={"name": "APIError", "data": {"message": "provider unavailable"}})]
    events = list(orch.build_stream("Build a table", conversation=tid))
    assert any(row.get("type") == "done" and not row.get("ok") for row in events)
    row = next(row for row in orch.list_threads() if row["id"] == tid)
    assert row["touched"] == []
    assert row["attempted"][0]["appId"] == orch.project().workspace.app_id


def test_request_rejected_before_admission_does_not_record_an_attempt(tmp_path, monkeypatch):
    from .test_a_dropped_mention_reaches_the_agents_prompt import _orch

    orch, _ = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    def rejected(*args, **kwargs):
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(orch, "_acquire_turn", rejected)
    assert list(orch.build_stream("Build a table", conversation=tid)) == [{"type": "done", "ok": False}]
    assert next(row for row in orch.list_threads() if row["id"] == tid)["attempted"] == []


def test_history_request_names_its_app_without_changing_the_selected_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as api

    from .test_a_dropped_mention_reaches_the_agents_prompt import _orch

    orch, _ = _orch(tmp_path)
    first = orch.project().workspace
    second = orch.create_app()
    app = orch._wm.app_workspace(orch._project_id, second["id"])
    app.append_history({"type": "user", "text": "second app"}, "thr_same")
    first.append_history({"type": "user", "text": "first app"}, "thr_same")
    orch.select_app(first.app_id)
    monkeypatch.setattr(api, "orchestrator", orch)
    response = TestClient(api.control_app).get(
        "/api/project/history", params={"conversation": "thr_same", "app": app.app_id})
    assert response.status_code == 200
    assert [row["text"] for row in response.json()["history"]] == ["second app"]
    assert orch.project().workspace.app_id == first.app_id


def test_app_return_preference_survives_reload_and_is_per_viewer():
    from .test_workbench_prefs import _run

    saved = {"project_one": {"app_a": "thr_one", "app_b": "thr_two"}}
    result = _run([
        {"viewer": "one", "op": "set", "name": "lastAppConversations", "value": saved},
        {"op": "reload"}, {"op": "get", "name": "lastAppConversations"},
        {"viewer": "two", "op": "get", "name": "lastAppConversations"},
        {"op": "set", "name": "lastAppConversations", "value": {"p": {"a": []}}},
        {"op": "get", "name": "lastAppConversations"},
    ])
    assert result == [True, None, saved, {}, False, {}]


@pytest.mark.parametrize("status", [403, 404])
def test_unavailable_preferred_conversation_uses_another_without_erasing_history(status):
    result = run({"historyUnavailable": status})
    assert result["routes"][-1] == "#/build/thr_many?app=app_a"
    assert result["available"] == ["thr_many", "thr_two", "thr_none"]
    assert result["saved"] is True
