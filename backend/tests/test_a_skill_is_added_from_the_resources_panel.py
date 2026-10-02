"""A Project skill is added from the resources panel and stands beside, or in for, Sage's own (#620).

ADR-0071. Three ways in besides the JSON route #619 added: an uploaded SKILL.md or zip, a git URL,
and a folder, SKILL.md or zip in one of the Project's Datasets. The skills Sage ships are read from
the template, never listed, and a Project skill may replace one of them: while it is on, the shim
hides the built-in. Otherwise a Project skill adds to Sage's and its instructions win.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extensions
from sage.assets.provider import Asset, DatasetFile, FileListing
from sage.router.model_control import ModelControl
from sage.router.models import Mode, Phase

from .test_a_projects_own_extensions_reach_the_next_turn import _sent
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build


def _md(name: str, description: str = "A test skill.") -> str:
    front = f"description: {description}\n" if description else ""
    return f"---\nname: {name}\n{front}---\nDo the thing.\n"


def _zip(files: dict[str, str | bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for path, body in files.items():
            archive.writestr(path, body)
    return out.getvalue()


def _fake_repo(tmp_path: Path, *names: str) -> Path:
    repo = tmp_path / "repo"
    for name in names:
        (repo / "template" / "skills" / name).mkdir(parents=True)
        (repo / "template" / "skills" / name / "SKILL.md").write_text(_md(name, f"Sage's {name}."))
    return repo


# ---- the skills Sage ships ---------------------------------------------------------------------

def test_the_built_in_skills_are_read_from_what_sage_ships():
    builtins = extensions.builtin_skills()
    assert {"data-table", "investigate-weak-signals"} <= set(builtins)
    assert all(builtins.values()), "every shipped skill has a description to show"


def test_a_skill_sage_adds_later_is_reserved_without_an_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(extensions, "_REPO", _fake_repo(tmp_path, "charts"))
    with pytest.raises(extensions.ExtensionError, match="Replaces charts"):
        extensions.add(tmp_path / "p", {"kind": "skill", "files": {"SKILL.md": _md("charts")}})


def test_a_project_skill_that_sage_later_ships_reads_as_shadowed(tmp_path, monkeypatch):
    """Measured on OpenCode 1.18.4: Sage's copy is the one offered, so the Project's is marked, and
    left out of the shim's catalogue so switching it off cannot hide Sage's."""
    root = tmp_path / "p"
    extensions.add(root, {"kind": "skill", "files": {"SKILL.md": _md("charts")}})
    extensions.add(root, {"kind": "skill", "files": {"SKILL.md": _md("tables")}})
    monkeypatch.setattr(extensions, "_REPO", _fake_repo(tmp_path, "charts"))

    listed = {e["name"]: e for e in extensions.list_extensions(root)}
    assert listed["charts"]["shadowed"] is True and "shadowed" not in listed["tables"]
    assert set(extensions.load_catalog(root).skills) == {"tables"}


# ---- the door ------------------------------------------------------------------------------------

def test_a_skill_with_no_description_is_refused_with_the_reason(tmp_path):
    with pytest.raises(extensions.ExtensionError, match="never offers it"):
        extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("plain", "")}})
    assert extensions.read_manifest(tmp_path) == []


def test_the_name_comes_from_the_frontmatter_when_none_is_given(tmp_path):
    entry = extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("house")}})
    assert entry["id"] == "skill:house"


def test_a_skill_may_replace_one_built_in_and_only_a_real_one(tmp_path):
    entry = extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("tables")},
                                      "replaces": "data-table"})
    assert entry["replaces"] == "data-table"
    with pytest.raises(extensions.ExtensionError, match="already replaces"):
        extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("grids")},
                                  "replaces": "data-table"})
    with pytest.raises(extensions.ExtensionError, match="not a skill Sage ships"):
        extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("x")},
                                  "replaces": "no-such-skill"})
    with pytest.raises(extensions.ExtensionError, match="not a skill Sage ships"):
        extensions.add(tmp_path, {"kind": "tool", "name": "t", "code": "x",
                                  "replaces": "data-table"})
    assert [e["name"] for e in extensions.read_manifest(tmp_path)] == ["tables"]


@pytest.mark.parametrize("section", ["design", "platform"])
def test_a_skill_may_replace_an_instruction_section(tmp_path, section):
    entry = extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("house")},
                                      "replaces": section})
    assert entry["replaces"] == section
    assert extensions.load_catalog(tmp_path).replaced == {section: "skill:house"}
    with pytest.raises(extensions.ExtensionError, match="already replaces"):
        extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("other")},
                                  "replaces": section})


# ---- the shim ------------------------------------------------------------------------------------

SYSTEM = ("<available_skills>\n"
          "  <skill>\n    <name>data-table</name>\n    <description>D.</description>\n  </skill>\n"
          "  <skill>\n    <name>tables</name>\n    <description>T.</description>\n  </skill>\n"
          "</available_skills>\n")


def _system(tmp_path: Path, off: frozenset[str] = frozenset()) -> str:
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.set_extensions(extensions.load_catalog(tmp_path))
    control.arm_extensions_off(off)
    return _sent(control, SYSTEM)[1]["messages"][0]["content"]


def test_a_replaced_built_in_is_hidden_while_its_replacement_is_on(tmp_path):
    extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("tables")},
                              "replaces": "data-table"})
    system = _system(tmp_path)
    assert "<name>tables</name>" in system and "<name>data-table</name>" not in system


def test_switching_the_replacement_off_brings_the_built_in_back(tmp_path):
    extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("tables")},
                              "replaces": "data-table"})
    system = _system(tmp_path, frozenset({"skill:tables"}))
    assert "<name>data-table</name>" in system and "<name>tables</name>" not in system
    assert "This Project added" not in system


def test_a_project_skill_is_told_not_to_override_sages_instructions(tmp_path):
    extensions.add(tmp_path, {"kind": "skill", "files": {"SKILL.md": _md("tables")}})
    system = _system(tmp_path)
    assert "<name>data-table</name>" in system and "<name>tables</name>" in system
    after = system.split("</available_skills>", 1)[1]
    assert "This Project added these skills: tables." in after
    assert "the instructions win" in after


# ---- upload and git ------------------------------------------------------------------------------

def _skills(filename: str, data: bytes, pick: list[str] | None = None) -> list[dict[str, str]]:
    skills, skipped = extensions.skills_in_files(extensions.upload_files(filename, data), filename,
                                                 pick)
    assert skipped == []
    return skills


def _md_files(**named: str) -> dict:
    return extensions.md_files({f"{n}.md": text.encode() for n, text in named.items()})


def test_a_zip_of_several_skill_folders_adds_each_with_only_its_own_files(tmp_path):
    data = _zip({"pack/a/SKILL.md": _md("a"), "pack/a/ref.md": "A",
                 "pack/a/b/SKILL.md": _md("b"), "pack/a/b/notes.md": "B",
                 "pack/README.md": "not a skill", "__MACOSX/pack/a/._SKILL.md": b"\x00\x01"})
    added = extensions.add_skills(tmp_path, _skills("pack.zip", data))
    files = {e["name"]: e["files"] for e in added}
    assert files == {"a": [".opencode/skills/a/SKILL.md", ".opencode/skills/a/ref.md"],
                     "b": [".opencode/skills/b/SKILL.md", ".opencode/skills/b/notes.md"]}


def test_a_single_skill_md_is_a_skill(tmp_path):
    added = extensions.add_skills(tmp_path, _skills("SKILL.md", _md("solo").encode()))
    assert [e["id"] for e in added] == ["skill:solo"]


@pytest.mark.parametrize(("filename", "data", "said"), [
    ("notes.txt", b"x", ".md files, or a .zip"),
    ("broken.zip", b"not a zip", "not a zip file"),
    ("empty.zip", _zip({"README.md": "x"}), "no SKILL.md"),
    ("binary.zip", _zip({"s/SKILL.md": b"\xff\xfe"}), "not a text"),
    ("plain.md", b"Just notes, no frontmatter.", "has a name in its frontmatter"),
])
def test_an_upload_sage_cannot_read_as_skills_says_why(filename, data, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        _skills(filename, data)


def test_an_import_adds_all_of_its_skills_or_none(tmp_path):
    skills = _skills("pack.zip", _zip({"a/SKILL.md": _md("a"), "b/SKILL.md": _md("b", "")}))
    with pytest.raises(extensions.ExtensionError, match="no description"):
        extensions.add_skills(tmp_path, skills)
    assert extensions.read_manifest(tmp_path) == []
    assert not (tmp_path / ".opencode" / "skills" / "a").exists()


def test_only_one_skill_can_replace_a_built_in(tmp_path):
    skills = _skills("pack.zip", _zip({"a/SKILL.md": _md("a"), "b/SKILL.md": _md("b")}))
    with pytest.raises(extensions.ExtensionError, match="holds 2 skills"):
        extensions.add_skills(tmp_path, skills, replaces="data-table")


# ---- what counts as a skill, and picking among them ----------------------------------------------

MIXED = {"README.md": "# Team skills", "src/app.py": "print(1)", "docs/guide.md": "---\nname: x\n",
         "skills/house/SKILL.md": _md("house"), "skills/house/ref/colors.md": "C",
         "skills/house/logo.png": b"\x89PNG\xff\xfe", "skills/brand/SKILL.md": _md("brand"),
         "tools/lint/SKILL.md": _md("lint", "")}


def test_in_a_mixed_zip_only_folders_holding_a_skill_md_are_skills():
    files = extensions.upload_files("repo.zip", _zip(MIXED))
    found = extensions.found_skills(files, "repo.zip")
    assert [(s["folder"], s["name"], s["files"]) for s in found] == [
        ("skills/brand", "brand", ["SKILL.md"]),
        ("skills/house", "house", ["SKILL.md", "ref/colors.md", "logo.png"]),
        ("tools/lint", "lint", ["SKILL.md"])]
    assert found[0]["description"] == "A test skill." and found[2]["description"] == ""


def test_a_preview_reads_each_skill_md_and_nothing_else():
    read = []
    files = {path: (lambda path=path, body=body: read.append(path) or (
                body if isinstance(body, bytes) else body.encode()))
             for path, body in MIXED.items()}
    assert [s["name"] for s in extensions.found_skills(files, "repo")] == ["brand", "house", "lint"]
    assert sorted(read) == ["skills/brand/SKILL.md", "skills/house/SKILL.md", "tools/lint/SKILL.md"]


def test_only_the_picked_skills_are_added_and_a_binary_file_is_left_out(tmp_path):
    files = extensions.upload_files("repo.zip", _zip(MIXED))
    skills, skipped = extensions.skills_in_files(files, "repo.zip", ["skills/house"])
    assert skipped == ["skills/house/logo.png"]
    [entry] = extensions.add_skills(tmp_path, skills)
    assert entry["files"] == [".opencode/skills/house/SKILL.md",
                              ".opencode/skills/house/ref/colors.md"]


def test_a_skill_md_in_any_case_is_a_skill_and_is_written_as_opencode_spells_it(tmp_path):
    files = extensions.upload_files("pack.zip", _zip({
        "a/skill.md": _md("a"), "a/ref.md": "R", "b/Skill.MD": _md("b")}))
    assert [s["files"] for s in extensions.found_skills(files, "pack.zip")] == [
        ["SKILL.md", "ref.md"], ["SKILL.md"]]
    added = extensions.add_skills(tmp_path, extensions.skills_in_files(files, "pack.zip")[0])
    assert {e["name"]: e["files"] for e in added} == {
        "a": [".opencode/skills/a/SKILL.md", ".opencode/skills/a/ref.md"],
        "b": [".opencode/skills/b/SKILL.md"]}


def test_two_spellings_of_skill_md_in_one_folder_are_refused():
    files = extensions.upload_files("pack.zip", _zip({"a/SKILL.md": _md("a"),
                                                      "a/skill.md": _md("a")}))
    with pytest.raises(extensions.ExtensionError, match="same file to OpenCode"):
        extensions.found_skills(files, "pack.zip")


@pytest.mark.parametrize(("pick", "said"), [(["skills/nope"], "no skill folder skills/nope"),
                                            ([], "Pick at least one")])
def test_a_pick_that_names_no_skill_is_refused(pick, said):
    files = extensions.upload_files("repo.zip", _zip(MIXED))
    with pytest.raises(extensions.ExtensionError, match=said):
        extensions.skills_in_files(files, "repo.zip", pick)


def test_several_md_files_each_naming_itself_are_several_skills(tmp_path):
    skills, _ = extensions.skills_in_files(_md_files(house=_md("house"), brand=_md("brand")), "f")
    assert sorted(e["name"] for e in extensions.add_skills(tmp_path, skills)) == ["brand", "house"]


def test_md_files_without_a_name_are_the_one_skills_own_files(tmp_path):
    files = _md_files(house=_md("house"), colors="Teal and slate.", voice="Plain words.")
    found = extensions.found_skills(files, "f")
    assert [(s["folder"], s["files"]) for s in found] == [
        ("house", ["SKILL.md", "colors.md", "voice.md"])]
    [entry] = extensions.add_skills(tmp_path, extensions.skills_in_files(files, "f")[0])
    assert entry["files"] == [".opencode/skills/house/SKILL.md",
                              ".opencode/skills/house/colors.md", ".opencode/skills/house/voice.md"]


@pytest.mark.parametrize(("named", "said"), [
    ({"a": _md("a"), "b": _md("b"), "notes": "N"}, "notes.md could belong to any of 2 skills"),
    ({"notes": "N", "more": "M"}, "None of these .md files has a name"),
])
def test_md_files_that_do_not_make_skills_say_why(named, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        _md_files(**named)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_git_url_is_cloned_and_its_commit_recorded(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    (origin / "skills" / "house").mkdir(parents=True)
    (origin / "skills" / "house" / "SKILL.md").write_text(_md("house"))
    git = ["git", "-C", str(origin), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "skills"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    # The https URL is rewritten to the local origin, so the real clone runs with no network.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://git.example/team/skills")

    (origin / "README.md").write_text("Not a skill.")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "readme"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()

    with extensions.git_files("https://git.example/team/skills") as (files, commit):
        skills, _ = extensions.skills_in_files(files, "repo")
    assert commit == head
    assert skills == [{"SKILL.md": _md("house")}]


def test_a_git_url_that_is_not_https_is_refused():
    with pytest.raises(extensions.ExtensionError, match="https://"):
        with extensions.git_files("file:///etc"):
            pass


# ---- a Dataset -----------------------------------------------------------------------------------

class _Datasets:
    """One mounted Dataset, read through the provider calls the orchestrator uses for any other."""

    def __init__(self, root: Path, *, truncated: bool = False) -> None:
        self.root, self.truncated, self.read = root, truncated, []

    def list_datasets(self, project_id):
        return [Asset(id="ds1", name="team-skills", mount_path=str(self.root))]

    def list_files(self, asset):
        return FileListing(files=[DatasetFile(p.relative_to(self.root).as_posix(), p.stat().st_size)
                                  for p in sorted(self.root.rglob("*")) if p.is_file()],
                           truncated=self.truncated)

    def download_file(self, asset, rel, dest):
        self.read.append(rel)
        dest.write_bytes((self.root / rel).read_bytes())
        return dest.stat().st_size


def _dataset_orch(tmp_path: Path, **kw):
    mount = tmp_path / "mount"
    (mount / "skills" / "house").mkdir(parents=True)
    (mount / "skills" / "house" / "SKILL.md").write_text(_md("house"))
    (mount / "skills" / "house" / "ref.md").write_text("R")
    (mount / "skills" / "other").mkdir()
    (mount / "skills" / "other" / "SKILL.md").write_text(_md("other"))
    (mount / "packs").mkdir()
    (mount / "packs" / "brand.zip").write_bytes(_zip({"brand/SKILL.md": _md("brand")}))
    (mount / "data.csv").write_text("a,b\n1,2\n")
    orch, oc, _ = _build(tmp_path / "w", [])
    datasets = _Datasets(mount, **kw)
    orch._assets = datasets
    return orch, oc, datasets


def _from_dataset(orch, dataset: str, path: str, replaces: str = "") -> list[dict]:
    with orch.dataset_skill_files(dataset, path) as (files, where, source):
        skills, _ = extensions.skills_in_files(files, where)
    return orch.add_skills(skills, replaces=replaces, source=source)


def test_a_skill_folder_in_a_dataset_is_copied_and_says_where_it_came_from(tmp_path):
    orch, oc, datasets = _dataset_orch(tmp_path)
    [entry] = _from_dataset(orch, "dataset:ds1", "skills/house")
    assert entry["files"] == [".opencode/skills/house/SKILL.md", ".opencode/skills/house/ref.md"]
    assert entry["source"] == {"type": "dataset", "dataset": "ds1", "name": "team-skills",
                               "path": "skills/house"}
    assert sorted(datasets.read) == ["skills/house/SKILL.md", "skills/house/ref.md"]
    assert oc.disposed, "the new skill reaches the next turn"


def test_a_folder_holding_several_skills_and_a_zip_in_a_dataset_both_work(tmp_path):
    orch, _, datasets = _dataset_orch(tmp_path)
    added = _from_dataset(orch, "ds1", "skills")
    assert sorted(e["name"] for e in added) == ["house", "other"]
    [brand] = _from_dataset(orch, "ds1", "packs/brand.zip", replaces="data-table")
    assert brand["replaces"] == "data-table"
    assert "data.csv" not in datasets.read


def test_a_folder_in_a_dataset_too_large_to_list_whole_is_refused_but_a_file_is_not(tmp_path):
    orch, _, _ = _dataset_orch(tmp_path, truncated=True)
    with pytest.raises(extensions.ExtensionError, match="too large to list whole"):
        _from_dataset(orch, "ds1", "skills/house")
    assert _from_dataset(orch, "ds1", "skills/house/SKILL.md")


# ---- the routes ----------------------------------------------------------------------------------

def test_the_routes_upload_import_and_list_per_scope(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, _, _ = _dataset_orch(tmp_path)
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)
    thread = orch.create_thread()["id"]

    up = client.post("/api/project/extensions/skills?filename=SKILL.md&replaces=data-table",
                     content=_md("tables").encode())
    assert [e["replaces"] for e in up.json()["items"]] == ["data-table"]
    bad = client.post("/api/project/extensions/skills?filename=SKILL.md",
                      content=_md("plain", "").encode())
    assert bad.status_code == 400 and "description" in bad.json()["error"]
    assert client.post("/api/project/extensions/skills/dataset",
                       json={"dataset": "ds1", "path": "skills/house"}).status_code == 200
    assert client.post("/api/project/extensions/skills/dataset",
                       json={"dataset": "nope", "path": ""}).status_code == 404
    assert client.post("/api/project/extensions/skills/git",
                       json={"url": "ssh://x"}).status_code == 400

    client.put("/api/project/extensions/skill:house/enabled",
               json={"thread": thread, "enabled": False})
    listed = client.get(f"/api/project/extensions?thread={thread}").json()
    assert {e["name"]: e["enabled"] for e in listed["items"]} == {"tables": True, "house": False}
    assert {e["name"]: e["enabled"]
            for e in client.get("/api/project/extensions").json()["items"]} == \
        {"tables": True, "house": True}
    assert "data-table" in {b["name"] for b in listed["builtinSkills"]}
    from sage.implementation_request import _OPTIONAL_BLOCKS
    assert [s["name"] for s in listed["builtinSections"]] == list(_OPTIONAL_BLOCKS)


def test_each_route_previews_without_adding_then_adds_only_the_pick(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, _, datasets = _dataset_orch(tmp_path)
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)

    seen = client.post("/api/project/extensions/skills/dataset",
                       json={"dataset": "ds1", "path": "skills", "preview": True}).json()
    assert [s["folder"] for s in seen["found"]] == ["house", "other"]
    assert datasets.read == ["skills/house/SKILL.md", "skills/other/SKILL.md"]
    assert client.get("/api/project/extensions").json()["items"] == []
    added = client.post("/api/project/extensions/skills/dataset",
                        json={"dataset": "ds1", "path": "skills", "pick": ["other"]}).json()
    assert [e["name"] for e in added["items"]] == ["other"] and added["skipped"] == []

    zipped = _zip({"a/SKILL.md": _md("a"), "a/icon.png": b"\xff\xfe", "b/SKILL.md": _md("b")})
    seen = client.post("/api/project/extensions/skills?filename=pack.zip&preview=true",
                       content=zipped).json()
    assert [s["folder"] for s in seen["found"]] == ["a", "b"]
    added = client.post("/api/project/extensions/skills?filename=pack.zip&pick=a",
                        content=zipped).json()
    assert [e["name"] for e in added["items"]] == ["a"] and added["skipped"] == ["a/icon.png"]

    files = {"house2.md": _md("house2"), "colors.md": "Teal."}
    seen = client.post("/api/project/extensions/skills/files",
                       json={"files": files, "preview": True}).json()
    assert [(s["name"], s["files"]) for s in seen["found"]] == [("house2", ["SKILL.md",
                                                                            "colors.md"])]
    added = client.post("/api/project/extensions/skills/files", json={"files": files}).json()
    assert [e["name"] for e in added["items"]] == ["house2"]
    bad = client.post("/api/project/extensions/skills/files", json={"files": {"x.txt": "x"}})
    assert bad.status_code == 400 and ".md" in bad.json()["error"]
