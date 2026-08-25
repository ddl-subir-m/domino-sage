from pathlib import Path

from sage.workspace.threads import ThreadStore, revert_denied_writes, snapshot_files, title_from_prompt


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
