"""Adding, changing or removing a Project skill is saved, like a change to the working set.

The skill lives in `.opencode/` at the Project root, which git tracks, but nothing committed it: it
reached git only when a chat turn, a build, a publish or a stop happened to save afterwards. A skill
added and the Workspace deleted was never there; a skill removed came back, because the next
Workspace clones HEAD. And "removes it for everyone" was untrue until that later save pushed.
"""
from __future__ import annotations

from pathlib import Path

from .test_a_rail_change_reaches_git import _quiet
from .test_chat_turn import _orch

SKILL = "---\nname: {name}\ndescription: A test skill.\n---\nDo the thing.\n"


def _skill(name: str) -> dict:
    return {"kind": "skill", "name": name, "files": {"SKILL.md": SKILL.format(name=name)}}


def test_adding_a_skill_saves_it(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    calls = _quiet(orch)

    orch.add_extension(_skill("alpha"))

    assert calls == ["chat (project skills)"]


def test_importing_skills_saves_them(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    calls = _quiet(orch)

    orch.add_skills([{"SKILL.md": SKILL.format(name="alpha")}], replaces="",
                    source={"type": "upload"})

    assert calls == ["chat (project skills)"]


def test_removing_a_skill_saves_it(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    orch.add_extension(_skill("alpha"))
    calls = _quiet(orch)

    assert orch.remove_extension("skill:alpha") is True

    assert calls == ["chat (project skills)"]


def test_removing_a_skill_that_is_not_there_saves_nothing(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    calls = _quiet(orch)

    assert orch.remove_extension("skill:never") is False

    assert calls == []


def test_changing_what_a_skill_replaces_saves_it(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    orch.add_extension(_skill("alpha"))
    calls = _quiet(orch)

    orch.set_skill_replaces("skill:alpha", "design")

    assert calls == ["chat (project skills)"]
