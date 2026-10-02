"""A Project skill is opened, re-pointed, and read again from where it came from; a private repo is
cloned with the credential the container holds for its host (#625).

ADR-0071. Updating rewrites the skill's files in place, so its id — and with it every switch on it
— and what it replaces survive. The credential is the one Domino wired for the Project's checkout,
found by `provision.credentials.extract_token`, and it reaches git only through a one-shot helper
and the child's environment.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extensions

from .test_a_skill_is_added_from_the_resources_panel import _dataset_orch, _md
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build

URL = "https://git.example/team/skills"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


@pytest.fixture(autouse=True)
def _no_credential(monkeypatch):
    monkeypatch.setattr(extensions, "_host_token", lambda host: None)


def _origin(tmp_path: Path, monkeypatch) -> tuple[Path, callable]:
    """A local repo standing in for `URL`, and a commit function answering the new HEAD."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git = ["git", "-C", str(origin), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", URL)

    def commit(files: dict[str, str]) -> str:
        for rel, text in files.items():
            (origin / rel).parent.mkdir(parents=True, exist_ok=True)
            (origin / rel).write_text(text)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "c"], check=True)
        return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    return origin, commit


def _add_from_git(orch, replaces: str = "") -> dict:
    with extensions.git_files(URL) as (files, commit):
        skills, _ = extensions.skills_in_files(files, URL)
    [entry] = orch.add_skills(skills, replaces=replaces,
                              source={"type": "git", "url": URL, "commit": commit})
    return entry


# ---- a private repo ------------------------------------------------------------------------------

def test_a_clone_asks_for_the_urls_host_and_hands_the_token_to_git_out_of_argv(tmp_path,
                                                                                monkeypatch):
    asked, ran = [], []
    monkeypatch.setattr(extensions, "_host_token", lambda host: asked.append(host) or "s3cret")

    def run(argv, **kw):
        ran.append((argv, kw.get("env") or {}))
        return subprocess.CompletedProcess(argv, 0, "abc123\n" if "rev-parse" in argv else "", "")
    monkeypatch.setattr(extensions.subprocess, "run", run)

    assert extensions.clone("https://GitHub.com/team/private.git", str(tmp_path)) == "abc123"
    assert asked == ["github.com"]
    argv, env = ran[0]
    assert "clone" in argv and "credential.helper=" in argv
    assert not any("s3cret" in a for a in argv)
    assert env["SAGE_CLONE_TOKEN"] == "s3cret" and env["GIT_TERMINAL_PROMPT"] == "0"


@needs_git
def test_the_one_shot_helper_answers_git_with_the_token_from_its_environment(monkeypatch):
    monkeypatch.setenv("SAGE_CLONE_TOKEN", "s3cret")
    out = subprocess.run(
        ["git", "-c", "credential.helper=", "-c", f"credential.helper={extensions._CLONE_HELPER}",
         "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    assert "username=x-access-token" in out.stdout and "password=s3cret" in out.stdout


@pytest.mark.parametrize(("token", "hint"), [(None, True), ("s3cret", False)])
def test_a_refused_clone_says_whether_a_credential_was_held_and_never_shows_it(monkeypatch,
                                                                               tmp_path, token,
                                                                               hint):
    monkeypatch.setattr(extensions, "_host_token", lambda host: token)

    def run(argv, **kw):
        raise subprocess.CalledProcessError(128, argv, stderr="fatal: could not read Username")
    monkeypatch.setattr(extensions.subprocess, "run", run)

    with pytest.raises(extensions.ExtensionError) as raised:
        extensions.clone("https://git.example/team/private", str(tmp_path))
    said = str(raised.value)
    assert "could not read Username" in said and "s3cret" not in said
    assert ("no git credential for git.example" in said) is hint


# ---- update from source --------------------------------------------------------------------------

@needs_git
def test_updating_a_git_skill_takes_the_new_commit_and_keeps_its_switches_and_replaces(
        tmp_path, monkeypatch):
    _, commit = _origin(tmp_path, monkeypatch)
    first = commit({"skills/house/SKILL.md": _md("house", "Old.")})
    orch, oc, _ = _build(tmp_path / "w", [])
    entry = _add_from_git(orch, replaces="data-table")
    assert entry["source"]["commit"] == first
    thread = orch.create_thread()["id"]
    orch.set_extension_enabled("skill:house", False, thread=thread)

    second = commit({"skills/house/SKILL.md": _md("house", "New."), "skills/house/ref.md": "R"})
    read = orch.update_skill_from_source("skill:house")

    assert read["previous"]["commit"] == first and read["item"]["source"]["commit"] == second
    assert read["item"]["replaces"] == "data-table"
    assert read["item"]["files"] == [".opencode/skills/house/SKILL.md",
                                     ".opencode/skills/house/ref.md"]
    assert [f["path"] for f in orch.skill_files("skill:house")] == ["SKILL.md", "ref.md"]
    assert "New." in orch.skill_files("skill:house")[0]["text"]
    [listed] = orch.list_extensions(thread=thread)
    assert listed["enabled"] is False, "the switch is keyed by id, which did not move"
    assert oc.disposed, "the update reaches the next turn"


@needs_git
def test_a_source_that_no_longer_holds_the_skill_leaves_it_as_it_was(tmp_path, monkeypatch):
    origin, commit = _origin(tmp_path, monkeypatch)
    commit({"skills/house/SKILL.md": _md("house")})
    orch, _, _ = _build(tmp_path / "w", [])
    _add_from_git(orch)
    (origin / "skills" / "house" / "SKILL.md").unlink()
    commit({"skills/other/SKILL.md": _md("other")})

    with pytest.raises(extensions.ExtensionError, match="no longer holds a skill called 'house'"):
        orch.update_skill_from_source("skill:house")
    assert [f["path"] for f in orch.skill_files("skill:house")] == ["SKILL.md"]


def test_a_rewrite_that_is_refused_puts_the_old_files_back(tmp_path):
    root = tmp_path / "p"
    extensions.add(root, {"kind": "skill", "files": {"SKILL.md": _md("house"), "a.md": "A"}})
    with pytest.raises(extensions.ExtensionError, match="no description"):
        extensions.update_skill(root, "skill:house", {"SKILL.md": _md("house", "")},
                                {"type": "upload"})
    assert [f["path"] for f in extensions.skill_files(root, "skill:house")] == ["SKILL.md", "a.md"]
    assert sorted(p.name for p in (root / ".opencode").iterdir()) == [
        "sage-extensions.json", "skills"], "nothing is left beside the skills folder"


def test_a_dataset_skill_is_read_again_from_its_folder(tmp_path):
    orch, _, _ = _dataset_orch(tmp_path)
    with orch.dataset_skill_files("ds1", "skills/house") as (files, where, source):
        skills, _ = extensions.skills_in_files(files, where)
    orch.add_skills(skills, replaces="", source=source)
    (tmp_path / "mount" / "skills" / "house" / "more.md").write_text("M")

    read = orch.update_skill_from_source("skill:house")
    assert read["item"]["source"] == source
    assert ".opencode/skills/house/more.md" in read["item"]["files"]


def test_an_uploaded_skill_has_nowhere_to_be_read_from_again(tmp_path):
    orch, _, _ = _build(tmp_path / "w", [])
    orch.add_skills([{"SKILL.md": _md("house")}], replaces="", source={"type": "upload"})
    with pytest.raises(extensions.ExtensionError, match="was uploaded"):
        orch.update_skill_from_source("skill:house")


# ---- replaces, after adding ----------------------------------------------------------------------

def test_replaces_is_changed_after_adding_and_one_holder_at_a_time(tmp_path):
    root = tmp_path / "p"
    extensions.add(root, {"kind": "skill", "files": {"SKILL.md": _md("house")}})
    extensions.add(root, {"kind": "skill", "files": {"SKILL.md": _md("grid")},
                          "replaces": "data-table"})
    with pytest.raises(extensions.ExtensionError, match="'grid' already replaces"):
        extensions.set_replaces(root, "skill:house", "data-table")
    assert extensions.set_replaces(root, "skill:grid", "data-table")["replaces"] == "data-table"
    assert extensions.set_replaces(root, "skill:house", "design")["replaces"] == "design"
    assert "replaces" not in extensions.set_replaces(root, "skill:house", "")
    with pytest.raises(extensions.ExtensionError, match="not a skill Sage ships"):
        extensions.set_replaces(root, "skill:house", "nope")
    with pytest.raises(KeyError):
        extensions.set_replaces(root, "skill:missing", "")


# ---- the routes ----------------------------------------------------------------------------------

def test_the_routes_read_files_set_replaces_and_update(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, _, _ = _dataset_orch(tmp_path)
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)
    client.post("/api/project/extensions/skills/dataset", json={"dataset": "ds1",
                                                                "path": "skills/house"})

    files = client.get("/api/project/extensions/skill:house/files").json()["files"]
    assert [f["path"] for f in files] == ["SKILL.md", "ref.md"]
    put = client.put("/api/project/extensions/skill:house/replaces", json={"replaces": "design"})
    assert put.json()["item"]["replaces"] == "design"
    assert client.put("/api/project/extensions/skill:house/replaces",
                      json={"replaces": "nope"}).status_code == 400
    assert client.post("/api/project/extensions/skill:house/update").status_code == 200
    for missing in (client.get("/api/project/extensions/skill:nope/files"),
                    client.put("/api/project/extensions/skill:nope/replaces", json={}),
                    client.post("/api/project/extensions/skill:nope/update")):
        assert missing.status_code == 404
