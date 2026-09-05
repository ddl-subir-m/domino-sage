"""Deleting a Conversation removes the talk, not just the row (ADR-0036).

The old delete wrote `"deleted": true` into `meta.json` and stopped. Every sibling file stayed —
`history.jsonl` above all — committed and pushed like the rest of `.sage/`, so a person who deleted
a conversation because of what was in it had moved a row off a rail and nothing else. The word on
the button did not admit to that.

Three things are asserted together here because each one alone would be a half-fix: the transcript
goes, the tombstone survives it (a plan document outlives its Conversation and its page has to tell
"deleted" from "never was" — #167), and the act reaches git on its own rather than waiting for a
turn that may never come.

The Artifacts are the one conditional. A Built App's committed handoff digest names
`examples/<threadId>/…` by path (ADR-0006), so removing those files under a live app would dangle a
document the dialog promises is untouched. They go only when nothing names them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

_HARNESS = Path(__file__).resolve().parent / "js" / "conversation_delete_harness.mjs"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _press_delete(saved: dict | None) -> dict:
    """Press Delete in the rail's menu and answer its DELETE with `saved`."""
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"saved": saved}),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


def _orch(tmp: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: nothing builds here
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
    )
    orch.project(start_preview=False, seed_app=False)
    return orch


def _store(orch: Orchestrator) -> ThreadStore:
    return ThreadStore(orch.project(start_preview=False, seed_app=False).record.path)


def _furnish(store: ThreadStore, title: str = "Desk exposure") -> str:
    """A Conversation with everything a real one accumulates: talk, chips, a manifest, a Build
    session id, and a chart under `examples/`."""
    tid = store.create(title)["id"]
    d = store.thread_dir(tid)
    (d / "history.jsonl").write_text('{"type":"user","text":"the thing I should not have pasted"}\n')
    (d / "context.json").write_text(json.dumps({"items": []}))
    (d / "artifacts.json").write_text(json.dumps({"items": [{"name": "chart.png"}]}))
    (d / "build-session-app_1.json").write_text(json.dumps({"session_id": "ses_1"}))
    ex = store.examples_dir(tid)
    ex.mkdir(parents=True, exist_ok=True)
    (ex / "chart.png").write_bytes(b"PNG")
    return tid


def _hand_off_to(store: ThreadStore, thread_id: str, app_id: str) -> None:
    """A confirmed handoff — the digest in that app now names this Thread's Artifacts by path."""
    (store.thread_dir(thread_id) / "handoff.json").write_text(
        json.dumps({"items": [{"appId": app_id, "status": "bound"}]}))


# ---- the transcript ----

def test_deleting_a_conversation_takes_its_transcript_off_disk(tmp_path: Path):
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)

    orch.delete_thread(tid)

    d = store.thread_dir(tid)
    assert not (d / "history.jsonl").exists()
    assert not (d / "context.json").exists()
    assert not (d / "artifacts.json").exists()
    assert not (d / "build-session-app_1.json").exists()
    # The directory itself stays, because the tombstone lives in it.
    assert (d / "meta.json").exists()


def test_the_tombstone_keeps_only_what_the_plan_page_needs(tmp_path: Path):
    """The title is the first 60 characters of the person's own first message, so it is transcript
    too. What is left has to answer "what became of this" and nothing else."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store, title="Why our Q3 margin fell off a cliff")

    orch.delete_thread(tid)

    row = json.loads((store.thread_dir(tid) / "meta.json").read_text())
    assert set(row) == {"id", "deleted", "updatedAt"}
    assert row["id"] == tid and row["deleted"] is True


def test_a_deleted_conversation_is_still_told_apart_from_one_that_never_was(tmp_path: Path):
    """#167. A plan document records the Conversation it was written in and outlives it, so the two
    are different sentences with different advice — and purging the files must not collapse them."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)

    orch.delete_thread(tid)

    assert store.is_deleted(tid) is True
    assert store.is_deleted("thr_neverexisted") is False
    assert store.get(tid) is None
    assert store.list() == []


def test_deleting_the_same_conversation_twice_still_answers_once(tmp_path: Path):
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)

    orch.delete_thread(tid)
    try:
        orch.delete_thread(tid)
    except KeyError:
        return
    raise AssertionError("the second delete should not have found a Conversation")


# ---- the Artifacts ----

def test_the_artifacts_go_when_no_built_app_names_them(tmp_path: Path):
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)

    orch.delete_thread(tid)

    assert not store.examples_dir(tid).exists()


def test_the_artifacts_stay_when_a_built_app_names_them(tmp_path: Path):
    """The app's own handoff digest names `examples/<threadId>/chart.png` by path and is committed
    in that app. Removing the file dangles a live document, and the dialog promises the apps it
    changed stay exactly as they are."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    app_id = orch.create_app()["id"]  # a real directory under apps/, as a handoff would leave
    _hand_off_to(store, tid, app_id)

    orch.delete_thread(tid)

    assert (store.examples_dir(tid) / "chart.png").exists()
    # The talk still goes. Keeping the charts is not keeping the conversation.
    assert not (store.thread_dir(tid) / "history.jsonl").exists()


def test_an_app_deleted_since_does_not_keep_the_artifacts_alive(tmp_path: Path):
    """Same guard `list_threads` applies: an entry naming an app that has since been deleted names
    nothing, so there is no document left to dangle."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    _hand_off_to(store, tid, "app_goneforever")

    orch.delete_thread(tid)

    assert not store.examples_dir(tid).exists()


# ---- reaching git ----

def test_deleting_a_conversation_saves_the_project_on_its_own(tmp_path: Path):
    """Without this the tombstone waits for the next turn or a graceful shutdown, and a hard stop
    in that window brings the whole conversation back on restart."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    saves: list[str] = []
    orch._save_to_git = lambda project, prompt: saves.append(prompt) or {  # type: ignore[method-assign]
        "type": "saved", "ok": True, "pushed": True, "detail": "pushed"}

    out = orch.delete_thread(tid)

    assert len(saves) == 1
    assert out["saved"]["ok"] is True


def test_a_save_that_did_not_reach_the_repo_is_reported_not_hidden(tmp_path: Path):
    """The files are already gone locally and the remote still has them, so a second Sage Builder
    goes on listing this Conversation. The person is told, because only they can judge it."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    orch._save_to_git = lambda project, prompt: {  # type: ignore[method-assign]
        "type": "saved", "ok": False, "pushed": False, "detail": "couldn't sync with the repo"}

    out = orch.delete_thread(tid)

    assert out["saved"]["ok"] is False
    assert "couldn't sync" in out["saved"]["detail"]
    # And the delete itself still happened: it is reported, not rolled back.
    assert store.is_deleted(tid) is True


# ---- the ones deleted before this change ----

def test_the_sweep_finishes_a_delete_that_predates_this_change(tmp_path: Path):
    """Every Conversation deleted by the old code still has its full transcript, on disk and on the
    remote. Those are the ones that matter, so the Builder finishes the job once at start."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    # Exactly what the old delete left behind: a tombstoned record beside every file.
    row = json.loads((store.thread_dir(tid) / "meta.json").read_text())
    row["deleted"] = True
    (store.thread_dir(tid) / "meta.json").write_text(json.dumps(row))

    orch.sweep_deleted_conversations()

    assert not (store.thread_dir(tid) / "history.jsonl").exists()
    assert not store.examples_dir(tid).exists()
    assert store.is_deleted(tid) is True


def test_the_sweep_leaves_a_live_conversation_alone(tmp_path: Path):
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)

    orch.sweep_deleted_conversations()

    assert (store.thread_dir(tid) / "history.jsonl").exists()
    assert (store.examples_dir(tid) / "chart.png").exists()
    assert [r["id"] for r in store.list()] == [tid]


def test_the_sweep_finds_nothing_left_the_second_time(tmp_path: Path):
    """It runs on every attach, so it has to be free after the first one."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    orch.delete_thread(tid)

    assert orch.sweep_deleted_conversations() == 0


def test_the_sweep_does_not_push(tmp_path: Path):
    """Two Builders starting together would both sweep and both commit — a merge at boot, over work
    either one can redo, with nobody watching to be told it failed. The next save carries it."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    row = json.loads((store.thread_dir(tid) / "meta.json").read_text())
    row["deleted"] = True
    (store.thread_dir(tid) / "meta.json").write_text(json.dumps(row))
    saves: list[str] = []
    orch._save_to_git = lambda project, prompt: saves.append(prompt)  # type: ignore[method-assign]

    assert orch.sweep_deleted_conversations() == 1
    assert saves == []


def test_a_second_sweep_does_not_take_the_artifacts_it_was_told_to_keep(tmp_path: Path):
    """The verdict is read off `handoff.json`, and the purge that honours it takes `handoff.json`.
    Recomputed from what is left, every later sweep would decide nothing names these files and
    delete them — the protection would last exactly one restart."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    app_id = orch.create_app()["id"]
    _hand_off_to(store, tid, app_id)
    orch.delete_thread(tid)
    assert (store.examples_dir(tid) / "chart.png").exists()

    orch.sweep_deleted_conversations()
    orch.sweep_deleted_conversations()

    assert (store.examples_dir(tid) / "chart.png").exists()


def test_the_sweep_gives_back_the_files_the_conversation_fetched(tmp_path: Path):
    """`context.json` is the only record of what a Conversation fetched, and the purge takes it.
    Left unreleased, those copies sit in scratch for the life of the Project and go on counting
    against the fetch cap."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    (store.thread_dir(tid) / "context.json").write_text(
        json.dumps({"items": [{"path": "data/desk.csv"}]}))
    row = json.loads((store.thread_dir(tid) / "meta.json").read_text())
    row["deleted"] = True
    (store.thread_dir(tid) / "meta.json").write_text(json.dumps(row))
    released: list[str] = []
    orch._release_chat_file = lambda project, path: released.append(path)  # type: ignore[method-assign]

    orch.sweep_deleted_conversations()

    assert released == ["data/desk.csv"]


# ---- what the person is told ----

@_needs_node
def test_the_dialog_says_what_goes_what_stays_and_what_git_keeps(tmp_path: Path):
    """"Removed for good" is a promise the old delete did not keep. The git-history clause is the
    limit of that promise and stays in the dialog: the person deleting because of what they pasted
    is exactly the reader a "learn more" popover would hide it from."""
    dialog = _press_delete({"ok": True, "pushed": True, "detail": "pushed"})

    assert dialog["title"] == "Delete this conversation?"
    assert "removed for good" in dialog["content"]
    assert "stay exactly as they are" in dialog["content"]
    assert "git history" in dialog["content"]
    assert dialog["okText"] == "Delete" and dialog["danger"] is True


@_needs_node
def test_pressing_delete_says_nothing_more_when_the_save_landed(tmp_path: Path):
    out = _press_delete({"ok": True, "pushed": True, "detail": "pushed"})

    assert out["deleted"] == ["t-1"]
    assert out["errors"] == []


@_needs_node
def test_a_save_that_did_not_land_is_said_out_loud(tmp_path: Path):
    """Invisible by construction otherwise: the files are gone locally and the rail redraws without
    the row, so an empty rail would read as a delete that finished."""
    out = _press_delete({"ok": False, "pushed": False, "detail": "couldn't sync with the repo"})

    assert out["deleted"] == ["t-1"]
    assert len(out["errors"]) == 1
    assert "couldn't sync with the repo" in out["errors"][0]
    assert "may come back" in out["errors"][0]


@_needs_node
def test_a_workspace_that_is_not_a_repo_is_not_an_error(tmp_path: Path):
    """`_save_to_git` answers None off a volume that is not a repo root — local dev, the /tmp
    spike. Nothing failed there, so nothing is reported."""
    out = _press_delete(None)

    assert out["errors"] == []


def test_an_app_deleted_later_lets_the_next_sweep_finish_the_job(tmp_path: Path):
    """The verdict stays a live question rather than being frozen onto the tombstone. Freeze it and
    the Artifacts of an app that is itself later deleted are stranded in the tree for good."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    app_id = orch.create_app()["id"]
    _hand_off_to(store, tid, app_id)
    orch.delete_thread(tid)
    assert (store.examples_dir(tid) / "chart.png").exists()

    orch.delete_app(app_id)
    orch.sweep_deleted_conversations()

    assert not store.examples_dir(tid).exists()
    assert not (store.thread_dir(tid) / "handoff.json").exists()


def test_the_sweep_reaches_a_thread_the_old_index_only_ever_orphaned(tmp_path: Path):
    """A Project still on `threads.json` has no `meta.json` to find. Without adopting that index
    first the sweep answers "nothing to do", and the adoption on the next rail scan then mints
    tombstones for exactly the directories the old delete orphaned."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    # What the pre-ADR-0008 delete left: the row dropped from the index, the directory untouched.
    (store.thread_dir(tid).parent.parent / "threads.json").write_text(json.dumps([]))
    (store.thread_dir(tid) / "meta.json").unlink()

    orch.sweep_deleted_conversations()

    assert store.is_deleted(tid) is True
    assert not (store.thread_dir(tid) / "history.jsonl").exists()


def test_one_unreadable_thread_does_not_take_the_whole_attach_down(tmp_path: Path):
    """The sweep runs after `self._project` is assigned, so a raise here would 500 whichever
    request triggered the attach and then be cached away — the migration never running again, and
    nothing saying why."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    (store.thread_dir(tid).parent / "not a thread id!").mkdir()
    row = json.loads((store.thread_dir(tid) / "meta.json").read_text())
    row["deleted"] = True
    (store.thread_dir(tid) / "meta.json").write_text(json.dumps(row))

    orch.sweep_deleted_conversations()  # must not raise

    assert not (store.thread_dir(tid) / "history.jsonl").exists()


def test_a_delete_during_a_build_turn_defers_its_commit(tmp_path: Path):
    """The save walks the tree and commits it. A build turn writing files at that moment would have
    its half-written tree committed, which is what the turn lock is for. Losing the race defers the
    commit to the idle timer; it never runs unguarded, and it never rolls the delete back."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _furnish(store)
    saves: list[str] = []
    orch._save_to_git = lambda project, prompt: saves.append(prompt)  # type: ignore[method-assign]
    orch._turn_lock.acquire()
    try:
        out = orch.delete_thread(tid)
    finally:
        orch._turn_lock.release()

    assert saves == []
    assert out["saved"] is None  # deferred, not failed: the UI reports nothing
    assert store.is_deleted(tid) is True
    assert not (store.thread_dir(tid) / "history.jsonl").exists()
