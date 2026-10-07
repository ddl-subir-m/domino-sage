"""A mounted Dataset chip is read through a link inside the Chat working folder (#675).

`@sales-playbooks` adds a chip whose path is the mount itself, `/mnt/data/sales-playbooks`. That
folder is outside the project, and `external_directory: deny` refuses `read`, `glob` and `bash cat`
there — measured on the pinned OpenCode 1.18.4. The same reads through a symlink INSIDE the project
succeed, because OpenCode checks the path it is given, not where a link points. So the chip line
hands the agent a path under the Chat working folder, and the working folder links it to the mount.
"""
from pathlib import Path

from sage.workspace.threads import ensure_chat_workdir, mounted_dataset_link

from .test_chat_turn import Turn, _orch

TID = "thr_aaaaaaaaaaaaaaaaaaaaa"


def _mount(tmp_path: Path, *parts: str) -> Path:
    d = tmp_path.joinpath("mnt", *parts)
    d.mkdir(parents=True)
    (d / "playbook.md").write_text("# battlecards\n")
    return d


def test_a_mounted_dataset_is_readable_through_the_chat_workdir(tmp_path: Path):
    mount = _mount(tmp_path, "data", "sales-playbooks")

    work = ensure_chat_workdir(tmp_path / "code", "# chat", thread_id=TID, mounts=[str(mount)])
    link = mounted_dataset_link(str(mount))

    assert not Path(link).is_absolute()
    assert (work / link / "playbook.md").read_text() == "# battlecards\n"
    # `public/data` is the app's tree. A Dataset link there would sit among the app's attachments.
    assert not link.startswith("public/")


def test_two_datasets_that_share_a_name_get_two_links(tmp_path: Path):
    """A Dataset of this project and one imported from another can carry the same name."""
    own = _mount(tmp_path, "data", "sales")
    imported = _mount(tmp_path, "imported", "data", "sales")

    assert mounted_dataset_link(str(own)) != mounted_dataset_link(str(imported))


def test_a_dataset_name_becomes_one_safe_path_segment(tmp_path: Path):
    link = mounted_dataset_link("/mnt/data/sales playbooks (2026)")

    assert len(Path(link).parts) == 2
    assert " " not in link and "(" not in link


def test_a_dataset_link_from_an_earlier_turn_is_pruned(tmp_path: Path):
    """The working folder outlives the Thread that last used it. A Dataset chip removed, or one
    from another Thread, must not stay readable from this turn's cwd."""
    old = _mount(tmp_path, "data", "old")
    new = _mount(tmp_path, "data", "new")
    ensure_chat_workdir(tmp_path / "code", "# chat", thread_id=TID, mounts=[str(old)])

    work = ensure_chat_workdir(tmp_path / "code", "# chat", thread_id=TID, mounts=[str(new)])

    assert not (work / mounted_dataset_link(str(old))).exists()
    assert not (work / mounted_dataset_link(str(old))).is_symlink()
    assert (work / mounted_dataset_link(str(new)) / "playbook.md").exists()


def test_a_real_directory_beside_the_links_is_left_alone(tmp_path: Path):
    """Only a symlink is ours to remove, as everywhere else in the working folder."""
    work = ensure_chat_workdir(tmp_path / "code", "# chat", thread_id=TID)
    theirs = work / Path(mounted_dataset_link("/mnt/data/x")).parent / "theirs"
    theirs.mkdir(parents=True)
    (theirs / "kept.md").write_text("kept\n")

    ensure_chat_workdir(tmp_path / "code", "# chat", thread_id=TID)

    assert (theirs / "kept.md").read_text() == "kept\n"


def test_the_turn_names_the_linked_path_and_lists_the_real_folder(tmp_path: Path):
    """The wiring: the chip line gives the agent the in-project path, and the file names it lists
    come from the mounted folder itself."""
    mount = _mount(tmp_path, "data", "sales-playbooks")
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "dataset", "name": "sales-playbooks", "path": str(mount)})

    list(orch.chat_stream(tid, "what do the @sales-playbooks say?"))

    prompt = oc.prompts[0]["text"]
    assert f"files at {mounted_dataset_link(str(mount))}." in prompt
    assert "Read these files: `playbook.md`." in prompt
    assert str(mount) not in prompt


def _pinned_file_turn(tmp_path: Path, monkeypatch, path: Path, prompt: str) -> dict:
    """One Chat turn on a Thread holding a single file chip at absolute `path`."""
    monkeypatch.setenv("DOMINO_DATASET_MOUNT_PATH", str(tmp_path / "mnt" / "data"))
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "file", "name": path.name, "path": str(path)})
    list(orch.chat_stream(tid, prompt))
    return oc.prompts[0]


def test_a_file_pinned_from_a_mounted_dataset_is_named_through_its_dataset_link(
        tmp_path: Path, monkeypatch):
    """One file pinned from a mounted Dataset sits outside the project just as its folder does, so
    it is named inside the link to the Dataset that holds it — the same link a Dataset chip gets."""
    mount = _mount(tmp_path, "data", "sales-playbooks", "q3").parent

    sent = _pinned_file_turn(tmp_path, monkeypatch, mount / "q3" / "playbook.md", "summarise it")

    assert f"at {mounted_dataset_link(str(mount))}/q3/playbook.md" in sent["text"]
    assert str(mount) not in sent["text"]


def test_an_at_named_file_from_a_mounted_dataset_is_attached_by_its_linked_path(
        tmp_path: Path, monkeypatch):
    mount = _mount(tmp_path, "data", "sales-playbooks", "q3").parent

    sent = _pinned_file_turn(tmp_path, monkeypatch, mount / "q3" / "playbook.md",
                             "what does @playbook.md say?")

    assert [a["path"] for a in sent["attachments"] or []] == [
        f"{mounted_dataset_link(str(mount))}/q3/playbook.md"]


def test_a_file_outside_every_mounted_dataset_keeps_its_path(tmp_path: Path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "notes.md").write_text("# notes\n")

    sent = _pinned_file_turn(tmp_path, monkeypatch, elsewhere / "notes.md",
                             "what do the @notes.md say?")

    assert f"at {elsewhere / 'notes.md'}" in sent["text"]
    assert [a["path"] for a in sent["attachments"] or []] == [str(elsewhere / "notes.md")]
