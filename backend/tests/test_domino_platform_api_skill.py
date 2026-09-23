"""The `domino-platform-api` skill is generated from `LESSONS_LEARNED.md`, not written twice (#490,
one-app pivot). `LESSONS_LEARNED.md` is the file people actually edit; this pins the skill's body to
it the way `test_sage_chat_prompt.py` pins the chat prompt to its AGENTS.md, so the two cannot drift
unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

from sage.orchestrator.app import (
    _PLATFORM_SKILL_DESCRIPTION,
    _PLATFORM_SKILL_NAME,
    _install_opencode_skills,
    _platform_api_skill_text,
)

_REPO = Path(__file__).resolve().parents[2]


def test_the_skill_body_is_lessons_learned_verbatim():
    text = _platform_api_skill_text(_REPO)
    assert text is not None
    body = (_REPO / "LESSONS_LEARNED.md").read_text(encoding="utf-8")
    assert text.endswith(body)


def test_the_frontmatter_names_the_skill_and_stays_inside_the_budget():
    text = _platform_api_skill_text(_REPO)
    front = re.match(r"^---\n(.*?)\n---\n\n", text, re.DOTALL)
    assert front, "no frontmatter — OpenCode would offer this skill to no turn"
    assert f"name: {_PLATFORM_SKILL_NAME}" in front.group(1)
    described = re.search(r"^description:[ \t]*(.*)$", front.group(1), re.MULTILINE)
    assert described and described.group(1).strip()
    assert len(_PLATFORM_SKILL_DESCRIPTION) <= 300


def test_an_unreadable_lessons_file_is_none_not_a_crash(tmp_path):
    assert _platform_api_skill_text(tmp_path / "nowhere") is None


def test_the_skill_lands_in_opencodes_global_slot(tmp_path):
    global_dir = tmp_path / "opencode"
    _install_opencode_skills(_REPO, global_dir)

    landed = global_dir / "skills" / _PLATFORM_SKILL_NAME / "SKILL.md"
    assert landed.is_file()
    assert landed.read_text() == _platform_api_skill_text(_REPO)
