"""A git-pulled skill can be registered by uploading the same files, without overwriting it."""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from sage import extensions


def _files(name: str) -> dict[str, str]:
    return {"SKILL.md": f"---\nname: {name}\ndescription: Chart design.\n---\nUse the palette.\n",
            "references/colors.md": "Purple: #543FDE\n"}


def _copy(root: Path, name: str) -> Path:
    folder = root / ".opencode/skills" / name
    for rel, text in _files(name).items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    return folder


def _upload(*names: str) -> list[dict[str, str]]:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name in names:
            for rel, text in _files(name).items():
                archive.writestr(f"{name}/{rel}", text)
    files = extensions.upload_files("skills.zip", data.getvalue())
    skills, skipped = extensions.skills_in_files(files, "skills.zip")
    assert skipped == []
    return skills


def test_upload_registers_an_identical_project_copy_and_adds_the_other_skills(tmp_path):
    folder = _copy(tmp_path, "domino-charting")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()}

    added = extensions.add_skills(tmp_path, _upload("domino-charting", "revops"))

    assert [e["name"] for e in added] == ["domino-charting", "revops"]
    assert extensions.read_manifest(tmp_path) == added
    assert added[0]["files"] == [".opencode/skills/domino-charting/SKILL.md",
                                  ".opencode/skills/domino-charting/references/colors.md"]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before
    assert "domino-charting" in extensions.load_catalog(tmp_path).skill_md


@pytest.mark.parametrize("difference", ["changed", "extra", "missing", "extra-directory"])
def test_upload_refuses_a_different_existing_folder_without_changing_it(tmp_path, difference):
    folder = _copy(tmp_path, "z-existing")
    if difference == "changed":
        (folder / "references/colors.md").write_text("User's color choices")
    elif difference == "extra":
        (folder / "private.txt").write_text("User's notes")
    elif difference == "extra-directory":
        (folder / "user-notes").mkdir()
    else:
        (folder / "references/colors.md").unlink()
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    with pytest.raises(extensions.ExtensionError, match="not Sage's"):
        extensions.add_skills(tmp_path, _upload("a-new", "z-existing"))

    assert extensions.read_manifest(tmp_path) == []
    assert not (tmp_path / ".opencode/skills/a-new").exists()
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before


def test_a_later_import_failure_keeps_an_adopted_folder_and_removes_only_new_files(tmp_path):
    folder = _copy(tmp_path, "a-existing")
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    bad = {"SKILL.md": "---\nname: z-bad\n---\nMissing description.\n"}

    with pytest.raises(extensions.ExtensionError, match="no description"):
        extensions.add_skills(tmp_path, [*_upload("a-existing", "b-new"), bad])

    assert extensions.read_manifest(tmp_path) == []
    assert not (tmp_path / ".opencode/skills/b-new").exists()
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before


def test_rollback_keeps_a_matching_folder_that_arrives_during_the_import(tmp_path, monkeypatch):
    write = extensions._write_skill

    def git_pulls_before_write(root, name, files, **kwargs):
        if name == "a-existing":
            _copy(root, name)
        return write(root, name, files, **kwargs)

    monkeypatch.setattr(extensions, "_write_skill", git_pulls_before_write)
    bad = {"SKILL.md": "---\nname: z-bad\n---\nMissing description.\n"}

    with pytest.raises(extensions.ExtensionError, match="no description"):
        extensions.add_skills(tmp_path, [*_upload("a-existing", "b-new"), bad])

    assert extensions.read_manifest(tmp_path) == []
    assert not (tmp_path / ".opencode/skills/b-new").exists()
    folder = tmp_path / ".opencode/skills/a-existing"
    assert {p.relative_to(folder).as_posix(): p.read_text()
            for p in folder.rglob("*") if p.is_file()} == _files("a-existing")


@pytest.mark.parametrize("linked", ["folder", "reference", "file", "skills", "opencode"])
def test_identical_content_reached_through_a_symlink_is_not_adopted(tmp_path, linked):
    root = tmp_path / "project"
    folder = _copy(root, "domino-charting")
    path = {"folder": folder, "reference": folder / "references",
            "file": folder / "SKILL.md", "skills": folder.parent,
            "opencode": folder.parent.parent}[linked]
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())

    with pytest.raises(extensions.ExtensionError, match="not Sage's"):
        extensions.add_skills(root, _upload("domino-charting"))

    assert extensions.read_manifest(root) == []
    assert path.is_symlink() and outside.exists()
