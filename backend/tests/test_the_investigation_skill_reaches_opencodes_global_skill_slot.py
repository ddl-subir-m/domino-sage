"""The seeded method reaches the slot OpenCode reads, and says so when it cannot.

A skill is only a method if it arrives. `~/.config/opencode/skills/<name>/SKILL.md` is the first
entry of 1.18.4's discovery list and the one slot Sage can fill — a Chat session runs under the
workspace volume, so the project slot is never ours. Everything here is asked of a TEMP global
directory: the real one is shared by every checkout on the machine, and a test that wrote to it
would rearrange another session's skills.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sage.orchestrator.app import _install_opencode_skills

SKILL = "investigate-weak-signals"


def _shipped() -> Path:
    return Path(__file__).resolve().parents[2] / "template" / "skills"


def _source(tmp_path: Path, *names: str, description: str = "How to do the thing.") -> Path:
    src = tmp_path / "template" / "skills"
    for name in names:
        (src / name).mkdir(parents=True)
        (src / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")
    return tmp_path


def test_the_shipped_skill_lands_in_the_global_slot_as_its_own_directory(tmp_path):
    """The whole point, and the thing no other surface can report: OpenCode globs
    `{skill,skills}/**/SKILL.md`, so the directory name and the file name are both load-bearing."""
    global_dir = tmp_path / "opencode"
    _install_opencode_skills(Path(__file__).resolve().parents[2], global_dir)

    landed = global_dir / "skills" / SKILL / "SKILL.md"
    assert landed.is_file()
    assert landed.read_text() == (_shipped() / SKILL / "SKILL.md").read_text()


def test_the_skill_is_copied_rather_than_linked(tmp_path):
    """A checkout that moves or a container that rebuilds must not leave a dangling link that
    fails the way MCP already fails — silently, with the skill simply absent."""
    global_dir = tmp_path / "opencode"
    _install_opencode_skills(Path(__file__).resolve().parents[2], global_dir)

    landed = global_dir / "skills" / SKILL
    assert not landed.is_symlink()
    assert not (landed / "SKILL.md").is_symlink()


def test_the_description_stays_inside_the_per_turn_budget():
    """The `description` is the whole per-turn cost of a skill — it is in the prompt of every turn
    whether or not the skill is loaded. The body is free until the model asks for it."""
    front = re.match(r"^---\n(.*?)\n---\s*\n", (_shipped() / SKILL / "SKILL.md").read_text(), re.DOTALL)
    assert front, "a SKILL.md with no frontmatter carries no description at all"
    described = re.search(r"^description:[ \t]*(.*)$", front.group(1), re.MULTILINE)
    assert described and described.group(1).strip()
    assert len(described.group(1).strip()) < 350


def test_the_shipped_skill_speaks_no_brand_name_and_no_unresolved_token():
    """`_install_opencode_skills` copies bytes. It does not voice them, the way
    `_install_opencode_config` voices `opencode.json` — so a product name or a `{token}` written
    here reaches the model verbatim on a white-labelled install. The repo has been bitten by an
    unvoiced template before; this is the pin, not a style rule."""
    body = (_shipped() / SKILL / "SKILL.md").read_text()

    assert "Sage" not in body
    assert not re.findall(r"\{[a-zA-Z][a-zA-Z0-9_]*\}", body)


def test_a_source_with_no_skills_is_loud_rather_than_silent(tmp_path, caplog):
    """Nothing to copy reads exactly like a clean install. This is the branch that says otherwise:
    an agent with no method still answers, just worse, and never mentions it."""
    (tmp_path / "template" / "skills").mkdir(parents=True)
    global_dir = tmp_path / "opencode"
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(tmp_path, global_dir)

    assert not (global_dir / "skills").exists()
    assert any("no Sage skills at" in r.message for r in caplog.records)


def test_a_source_that_cannot_be_read_is_loud_rather_than_silent(tmp_path, caplog):
    global_dir = tmp_path / "opencode"
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(tmp_path / "nowhere", global_dir)

    assert not (global_dir / "skills").exists()
    assert any("could NOT read" in r.message for r in caplog.records)


def test_a_renamed_skill_does_not_linger_in_a_slot_shared_by_every_checkout(tmp_path, caplog):
    """`_install_opencode_tools` copies and never prunes, which is harmless for `.ts` files whose
    names never change. A skill is a DIRECTORY: rename one and the old copy keeps costing its
    description on every turn, teaching a method the prompt no longer matches."""
    global_dir = tmp_path / "opencode"
    _install_opencode_skills(_source(tmp_path / "before", "old-name"), global_dir)
    assert (global_dir / "skills" / "old-name" / "SKILL.md").is_file()

    with caplog.at_level(logging.WARNING):
        _install_opencode_skills(_source(tmp_path / "after", "new-name"), global_dir)

    assert (global_dir / "skills" / "new-name" / "SKILL.md").is_file()
    assert not (global_dir / "skills" / "old-name").exists()
    assert any("pruned old-name" in r.getMessage() for r in caplog.records)


def test_a_half_finished_install_still_records_what_it_may_have_written(tmp_path, monkeypatch):
    """A name that lands and is never recorded can never be pruned — the lingering directory this
    function exists to prevent, arriving by the one route it does not watch. So the manifest is
    widened before the copies and narrowed after them."""
    import shutil

    global_dir = tmp_path / "opencode"
    source = _source(tmp_path / "src", "first", "second")
    real = shutil.copytree

    def fail_on_second(src, dst, *a, **kw):
        if Path(src).name == "second":
            raise OSError("no space left on device")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(shutil, "copytree", fail_on_second)
    _install_opencode_skills(source, global_dir)
    monkeypatch.undo()

    assert json.loads((global_dir / "skills" / ".sage-installed.json").read_text()) \
        == ["first", "second"]
    _install_opencode_skills(_source(tmp_path / "later", "renamed"), global_dir)
    assert not (global_dir / "skills" / "first").exists()


def test_replacing_a_skill_sage_did_not_write_is_said_out_loud(tmp_path, caplog):
    """A name collision in a slot shared by every checkout is a person's own skill being replaced.
    The replacement is right — a Sage skill that refuses to install is the worse failure — and the
    line is what makes it findable afterwards."""
    global_dir = tmp_path / "opencode"
    (global_dir / "skills" / "ours").mkdir(parents=True)
    (global_dir / "skills" / "ours" / "SKILL.md").write_text("---\nname: ours\ndescription: X\n---\n")

    with caplog.at_level(logging.WARNING):
        _install_opencode_skills(_source(tmp_path / "src", "ours"), global_dir)

    assert any("was not installed by Sage" in r.getMessage() for r in caplog.records)


def test_a_prune_that_could_not_finish_keeps_the_name_it_failed_on(tmp_path, monkeypatch, caplog):
    """A directory that survives its own `rmtree` and drops out of the manifest can never be pruned
    again — it is on disk, it is not ours any more as far as the next run can tell, and it goes on
    costing its description on every turn. So a failed prune keeps the name."""
    import shutil

    global_dir = tmp_path / "opencode"
    _install_opencode_skills(_source(tmp_path / "before", "old-name"), global_dir)
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **kw: None)
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(_source(tmp_path / "after", "new-name"), global_dir)
    monkeypatch.undo()

    assert json.loads((global_dir / "skills" / ".sage-installed.json").read_text()) \
        == ["new-name", "old-name"]
    assert any("could NOT prune old-name" in r.getMessage() for r in caplog.records)


def test_a_manifest_entry_that_is_not_a_plain_name_is_never_used_as_a_path(tmp_path):
    """Every manifest entry becomes a path component of an `rmtree`. `dest / ""` and `dest / "."`
    are `dest` ITSELF, so a manifest truncated to `[""]` would take the whole shared skills
    directory — the person's own skills included — on the next boot."""
    global_dir = tmp_path / "opencode"
    _install_opencode_skills(_source(tmp_path / "before", "ours"), global_dir)
    theirs = global_dir / "skills" / "someones-own"
    theirs.mkdir()
    (theirs / "SKILL.md").write_text("---\nname: someones-own\ndescription: Theirs.\n---\n")
    (global_dir / "skills" / ".sage-installed.json").write_text(json.dumps(["", ".", "../skills"]))

    _install_opencode_skills(_source(tmp_path / "after", "ours"), global_dir)

    assert (theirs / "SKILL.md").is_file()
    assert (global_dir / "skills" / "ours" / "SKILL.md").is_file()


def test_a_skill_sage_never_installed_is_left_alone(tmp_path):
    """The slot is shared. On a dev machine a person's own skills sit in this directory, and a
    prune that reads the directory instead of its own manifest would take them."""
    global_dir = tmp_path / "opencode"
    theirs = global_dir / "skills" / "someones-own"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("---\nname: someones-own\ndescription: Theirs.\n---\n")

    _install_opencode_skills(_source(tmp_path / "before", "ours"), global_dir)
    _install_opencode_skills(_source(tmp_path / "after", "ours-renamed"), global_dir)

    assert (theirs / "SKILL.md").is_file()
    assert json.loads((global_dir / "skills" / ".sage-installed.json").read_text()) \
        == ["ours-renamed"]
