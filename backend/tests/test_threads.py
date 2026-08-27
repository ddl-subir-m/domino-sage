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


def test_the_turn_snapshot_does_not_read_a_dataset_file_fetched_for_chat(tmp_path: Path):
    """Chat fetches a Dataset file into `.sage/scratch/datasets/` to answer a question about it, so
    skipping only `public/data/` moved the whole transactions CSV back into every turn's snapshot —
    read across the mount before the turn and again after it."""
    mount = tmp_path / "mnt" / "data" / "clickstream"
    mount.mkdir(parents=True)
    real = mount / "transformed_cc_transactions.csv"
    real.write_text("amount,merchant\n12.30,coffee\n")
    fetched = tmp_path / ".sage" / "scratch" / "datasets" / "clickstream" / real.name
    fetched.parent.mkdir(parents=True)
    fetched.symlink_to(real)

    assert ".sage/scratch/datasets/clickstream/transformed_cc_transactions.csv" not in snapshot_files(tmp_path)


def test_revert_does_not_delete_a_fetched_or_uploaded_file(tmp_path: Path):
    """Neither tree is a path a Chat turn may write, so revert treated both as denied writes: a file
    the `before` snapshot had not read was unlinked at the end of the turn, taking it out of the rail
    while the chip that named it stayed."""
    tid = "thr_abc"
    fetched = tmp_path / ".sage" / "scratch" / "datasets" / "clickstream" / "rows.csv"
    fetched.parent.mkdir(parents=True)
    fetched.write_text("a,b\n1,2\n")
    upload = tmp_path / ".sage" / "scratch" / "notes.csv"
    upload.write_text("x")

    assert revert_denied_writes(tmp_path, tid, before={}) == []
    assert fetched.is_file()
    assert upload.is_file()


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
    path. Chat stands at the Project root and the data belongs to the Built App one directory
    down, so without the link the path resolves to nothing where the agent stands — a file the
    person can see in the rail cannot be opened at all."""
    app = tmp_path / "apps" / "app_one"
    rel = "public/data/clickstream/clean_cc_transactions.csv"
    (app / rel).parent.mkdir(parents=True)
    (app / rel).write_text("amount,merchant\n12.30,coffee\n")
    (app / "public" / "vite.svg").write_text("<svg/>")

    work = ensure_chat_workdir(tmp_path, "# chat", data_dir=app / "public" / "data")

    assert (work / rel).read_text().startswith("amount,merchant")
    # Only the data is linked. The rest of public/ belongs to the app, which Chat does not read.
    assert not (work / "public" / "vite.svg").exists()


def test_the_chat_workdir_link_survives_a_second_turn(tmp_path: Path):
    """It is built on every turn, and the second call must not trip over its own symlink."""
    data = tmp_path / "apps" / "app_one" / "public" / "data"
    ensure_chat_workdir(tmp_path, "# chat", data_dir=data)
    work = ensure_chat_workdir(tmp_path, "# chat", data_dir=data)

    rel = "public/data/clickstream/late.csv"
    (data / "clickstream").mkdir(parents=True, exist_ok=True)
    (data / "clickstream" / "late.csv").write_text("a\n1\n")
    # Attached after the workdir was built: the link is to the directory, so it is already there.
    assert (work / rel).read_text() == "a\n1\n"


# ---- one record per Thread (#64) -------------------------------------------------


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _restore(root: Path, tree: dict[str, bytes]) -> None:
    for p in list(root.rglob("*")):
        if p.is_file():
            p.unlink()
    for rel, body in tree.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)


def test_two_builders_in_one_project_both_keep_their_thread(tmp_path: Path):
    """Two viewers in one Project are two Sage Builders, and two Builders are two processes over
    one directory. Neither sees the other's write before making its own, and the filesystem keeps
    the last write of each FILE. Replayed here: both Builders start from the same workspace, and
    the second one's files land on top. One shared index meant the second list replaced the first
    and a Thread whose history was still on disk vanished from the rail."""
    start = _tree(tmp_path)
    ThreadStore(tmp_path).create(title="Gross exposure")
    mine = _tree(tmp_path)

    _restore(tmp_path, start)
    ThreadStore(tmp_path).create(title="Rates by desk")
    for rel, body in mine.items():
        if not (tmp_path / rel).exists():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(body)

    assert {t["title"] for t in ThreadStore(tmp_path).list()} == {"Gross exposure", "Rates by desk"}


def test_a_thread_keeps_its_title_pin_and_time_off_the_old_index(tmp_path: Path):
    """An upgraded Project's Threads were only ever written to `.sage/threads.json`."""
    (tmp_path / ".sage" / "threads" / "thr_old").mkdir(parents=True)
    (tmp_path / ".sage" / "threads.json").write_text(
        '[{"id": "thr_old", "title": "Gross exposure", "createdAt": "2026-08-01T10:00:00Z",'
        ' "updatedAt": "2026-08-02T11:00:00Z", "pinned": true}]'
    )
    row = ThreadStore(tmp_path).get("thr_old")
    assert row["title"] == "Gross exposure"
    assert row["pinned"] is True
    assert row["updatedAt"] == "2026-08-02T11:00:00Z"
    assert [t["id"] for t in ThreadStore(tmp_path).list()] == ["thr_old"]


def test_a_thread_deleted_before_the_upgrade_does_not_come_back(tmp_path: Path):
    """The old delete dropped the index row and left `threads/<id>/` on disk, so a scan that
    trusted the directory alone would put a deleted conversation back in the rail."""
    (tmp_path / ".sage" / "threads" / "thr_gone").mkdir(parents=True)
    (tmp_path / ".sage" / "threads.json").write_text("[]")
    assert ThreadStore(tmp_path).list() == []
    assert ThreadStore(tmp_path).get("thr_gone") is None


def test_a_deleted_thread_does_not_come_back_from_the_scan(tmp_path: Path):
    store = ThreadStore(tmp_path)
    row = store.create(title="Gross exposure")
    assert store.delete(row["id"]) is True
    assert ThreadStore(tmp_path).list() == []


def test_an_unreadable_old_index_is_not_an_empty_one(tmp_path: Path):
    """The index was truncated in place with two Builders racing it, so a half-written read is
    this change's own failure mode. Reading it as "no Threads" would tombstone every Thread and
    then delete the only record of them, turning a bad read into permanent loss."""
    (tmp_path / ".sage" / "threads" / "thr_a").mkdir(parents=True)
    index = tmp_path / ".sage" / "threads.json"
    index.write_text('[{"id": "thr_a", "title": "Gross exposure", "updat')

    assert ThreadStore(tmp_path).list() == []
    assert index.exists()
    assert not (tmp_path / ".sage" / "threads" / "thr_a" / "meta.json").exists()

    index.write_text('[{"id": "thr_a", "title": "Gross exposure", "updatedAt": "2026-08-02T11:00:00Z"}]')
    assert [t["title"] for t in ThreadStore(tmp_path).list()] == ["Gross exposure"]


def test_the_rail_is_ordered_by_last_update(tmp_path: Path, monkeypatch):
    """Newest first, as the index's insertion order used to read — but a Thread answered again
    comes back to the top, which is what the rail's day buckets already claimed."""
    import sage.workspace.threads as threads_mod

    store = ThreadStore(tmp_path)
    first = store.create(title="first")
    second = store.create(title="second")
    assert [t["id"] for t in store.list()] == [second["id"], first["id"]]
    monkeypatch.setattr(threads_mod, "_now", lambda: "2099-01-01T00:00:00Z")
    store.touch(first["id"])
    assert [t["id"] for t in store.list()] == [first["id"], second["id"]]


def test_two_threads_made_in_one_millisecond_still_have_an_order(tmp_path: Path):
    """`createdAt` has one-second resolution and two creates land in the same millisecond about a
    third of the time, so without a strictly increasing id the OLDEST Thread — the one an upgraded
    Project's untagged Build history is handed to — would be picked by coin flip."""
    ids = [ThreadStore(tmp_path).create()["id"] for _ in range(20)]
    assert ids == sorted(ids)
