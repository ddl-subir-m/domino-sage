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
