from pathlib import Path

from sage.workspace.threads import (
    ThreadStore,
    ensure_chat_workdir,
    revert_denied_writes,
    snapshot_files,
    title_from_prompt,
)


def test_pin_rename_delete_thread(tmp_path: Path):
    store = ThreadStore(tmp_path)
    row = store.create(title="Gross exposure")
    updated = store.update(row["id"], pinned=True, title="Rates by desk")
    assert updated["pinned"] is True
    assert updated["title"] == "Rates by desk"
    assert store.delete(row["id"]) is True
    assert store.get(row["id"]) is None
    assert store.delete(row["id"]) is False


def test_create_thread_does_not_touch_build_history(tmp_path: Path):
    store = ThreadStore(tmp_path)
    build_history = tmp_path / ".sage" / "history.jsonl"
    build_history.parent.mkdir(parents=True)
    build_history.write_text('{"type":"user","text":"build me a dashboard"}\n')

    row = store.create()
    store.append_history(row["id"], {"type": "user", "text": "what's in this CSV?"})

    assert build_history.read_text() == '{"type":"user","text":"build me a dashboard"}\n'
    assert store.read_history(row["id"])[0]["text"] == "what's in this CSV?"
    assert (tmp_path / "examples" / row["id"]).is_dir()


def test_chips_persist_on_the_user_event_after_removal(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread = store.create()
    chip = store.add_context(thread["id"], {"kind": "file", "name": "positions.csv",
                                            "path": "public/data/positions.csv"})
    store.append_history(thread["id"], {
        "type": "user", "text": "summarise this", "contextIds": [chip["id"]],
    })
    assert store.remove_context(thread["id"], chip["id"]) is True
    assert store.read_context(thread["id"])["items"] == []
    assert store.read_history(thread["id"])[0]["contextIds"] == [chip["id"]]


def test_record_artifact_does_not_land_in_context(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread = store.create()
    path = f"examples/{thread['id']}/exposure.table.json"
    row = store.record_artifact(thread["id"], path=path)
    assert row["kind"] == "table"
    assert store.read_context(thread["id"])["items"] == []
    store.record_artifact(thread["id"], path=path)
    assert len(store.read_artifacts(thread["id"])) == 2
    assert store.read_context(thread["id"])["items"] == []


def test_read_context_drops_sage_added_artifacts(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread = store.create()
    store.add_context(thread["id"], {
        "kind": "file", "name": "desk.csv", "path": ".sage/scratch/desk.csv", "addedBy": "user",
    })
    store.add_context(thread["id"], {
        "kind": "artifact", "name": "desks.png",
        "path": f"examples/{thread['id']}/desks.png", "addedBy": "sage",
    })
    items = store.read_context(thread["id"])["items"]
    assert [i["kind"] for i in items] == ["file"]
    store.add_context(thread["id"], {
        "kind": "artifact", "name": "desks.png",
        "path": f"examples/{thread['id']}/desks.png", "addedBy": "user",
    })
    kinds = [i["kind"] for i in store.read_context(thread["id"])["items"]]
    assert kinds == ["file", "artifact"]


def test_title_from_prompt_truncates():
    assert title_from_prompt("  what's our exposure?  ") == "what's our exposure?"
    long = "x" * 80
    assert title_from_prompt(long).endswith("…")
    assert len(title_from_prompt(long)) == 60


def test_revert_denied_writes_restores_src(tmp_path: Path):
    tid = "thr_01abc"
    src = tmp_path / "src"
    src.mkdir()
    app = src / "App.tsx"
    app.write_text("original")
    (tmp_path / "examples" / tid).mkdir(parents=True)
    before = snapshot_files(tmp_path)
    app.write_text("agent was here")
    (tmp_path / "examples" / tid / "chart.png").write_bytes(b"png")

    reverted = revert_denied_writes(tmp_path, tid, before)
    assert "src/App.tsx" in reverted
    assert app.read_text() == "original"
    assert (tmp_path / "examples" / tid / "chart.png").read_bytes() == b"png"


def test_the_turn_snapshot_does_not_read_attached_dataset_bytes(tmp_path: Path):
    """An attached file is a symlink into /mnt/data. Snapshotting it read the whole Dataset across
    the mount, twice per Chat turn, for a file the turn may never touch."""
    mount = tmp_path / "mnt" / "data" / "clickstream"
    mount.mkdir(parents=True)
    real = mount / "clean_cc_transactions.csv"
    real.write_text("amount,merchant\n12.30,coffee\n")
    attached = tmp_path / "public" / "data" / "clickstream" / "clean_cc_transactions.csv"
    attached.parent.mkdir(parents=True)
    attached.symlink_to(real)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("original")

    snap = snapshot_files(tmp_path)

    assert "public/data/clickstream/clean_cc_transactions.csv" not in snap
    assert snap["src/App.tsx"] == b"original"


def test_a_src_data_folder_is_still_snapshotted(tmp_path: Path):
    """Only the attachment tree is skipped. `src/data/` is the agent's own, and a write there is
    exactly what revert exists for."""
    (tmp_path / "src" / "data").mkdir(parents=True)
    (tmp_path / "src" / "data" / "rows.ts").write_text("export const rows = []")

    assert snapshot_files(tmp_path)["src/data/rows.ts"] == b"export const rows = []"


def test_handoff_suggested_once_and_suppress(tmp_path: Path):
    store = ThreadStore(tmp_path)
    row = store.create()
    tid = row["id"]
    assert store.read_handoff(tid) is None
    first = store.mark_handoff_suggested(tid)
    second = store.mark_handoff_suggested(tid)
    assert first["suggestedAt"] == second["suggestedAt"]
    assert first["status"] == "suggested"
    suppressed = store.suppress_handoff(tid)
    assert suppressed["suppressed"] is True
    assert suppressed["status"] == "suppressed"
    assert suppressed["suggestedAt"] == first["suggestedAt"]


def test_an_attached_dataset_file_is_readable_from_the_chat_workdir(tmp_path: Path):
    """The turn prompt names the file `public/data/<slug>/<file>` and tells the agent to read that
    path. Chat's cwd is not the workspace root, so without the link the path resolves to nothing
    where the agent stands — a file the person can see in the rail cannot be opened at all."""
    rel = "public/data/clickstream/clean_cc_transactions.csv"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text("amount,merchant\n12.30,coffee\n")
    (tmp_path / "public" / "vite.svg").write_text("<svg/>")

    work = ensure_chat_workdir(tmp_path, "# chat")

    assert (work / rel).read_text().startswith("amount,merchant")
    # Only the data is linked. The rest of public/ belongs to the app, which Chat does not read.
    assert not (work / "public" / "vite.svg").exists()


def test_the_chat_workdir_link_survives_a_second_turn(tmp_path: Path):
    """It is built on every turn, and the second call must not trip over its own symlink."""
    ensure_chat_workdir(tmp_path, "# chat")
    work = ensure_chat_workdir(tmp_path, "# chat")

    rel = "public/data/clickstream/late.csv"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("a\n1\n")
    # Attached after the workdir was built: the link is to the directory, so it is already there.
    assert (work / rel).read_text() == "a\n1\n"
