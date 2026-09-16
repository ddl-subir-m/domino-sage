"""A SKILL.md with no frontmatter `description` is loaded and offered to nobody.

Measured on the pinned opencode-ai@1.18.4: `description` is optional in the skill schema, so the
file loads, appears in `GET /skill`, and sits on disk looking installed — and the block that lists
skills for the model is `skills.filter((s) => s.description !== undefined)`. Nothing is logged. It
is the `live_read.ts` import failure again: every surface healthy, the model offered nothing.

Install is the only place Sage can see it, so install is where it gets said out loud.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sage.orchestrator.app import _install_opencode_skills, _skill_description


def _source(tmp_path: Path, name: str, front: str) -> Path:
    (tmp_path / "template" / "skills" / name).mkdir(parents=True)
    (tmp_path / "template" / "skills" / name / "SKILL.md").write_text(
        f"---\n{front}\n---\n\n# {name}\n\nBody.\n")
    return tmp_path


def test_a_skill_with_no_description_is_named_in_an_error(tmp_path, caplog):
    source = _source(tmp_path, "no-description", "name: no-description")
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(source, tmp_path / "opencode")

    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("no-description" in m and "no usable frontmatter description" in m for m in said)


def test_the_undescribed_skill_is_still_installed_so_diag_can_see_it_too(tmp_path, caplog):
    """Loud, not withheld. Refusing the copy would make the skill absent from `GET /skill` as well,
    and the diag row would then agree with the prompt — two surfaces saying "no such skill" and
    neither of them saying why."""
    source = _source(tmp_path, "no-description", "name: no-description")
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(source, tmp_path / "opencode")

    assert (tmp_path / "opencode" / "skills" / "no-description" / "SKILL.md").is_file()


def test_a_described_skill_says_nothing(tmp_path, caplog):
    """The other half of the plant: a guard that fires on a healthy file is not a guard."""
    source = _source(tmp_path, "described", "name: described\ndescription: Does a thing.")
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(source, tmp_path / "opencode")

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_empty_description_counts_as_none(tmp_path, caplog):
    """`description:` with nothing after it parses as null, which is `undefined` to that filter."""
    source = _source(tmp_path, "blank", "name: blank\ndescription:")
    with caplog.at_level(logging.ERROR):
        _install_opencode_skills(source, tmp_path / "opencode")

    assert any("no usable frontmatter description" in r.getMessage() for r in caplog.records)


def test_a_file_with_no_frontmatter_at_all_counts_as_none(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Just a heading\n")
    assert _skill_description(tmp_path / "SKILL.md") == ""


def test_a_quoted_description_is_read_without_its_quotes(tmp_path):
    (tmp_path / "SKILL.md").write_text('---\nname: q\ndescription: "Does a thing."\n---\n')
    assert _skill_description(tmp_path / "SKILL.md") == "Does a thing."


def test_a_crlf_file_is_not_called_undescribed(tmp_path):
    """A false alarm about a file that is fine is a witness not worth having: the next person reads
    a loud error, checks the skill, finds a description, and stops believing the line."""
    (tmp_path / "SKILL.md").write_bytes(
        b"---\r\nname: c\r\ndescription: Does a thing.\r\n---\r\n\r\n# c\r\n")
    assert _skill_description(tmp_path / "SKILL.md") == "Does a thing."


def test_a_frontmatter_block_with_nothing_after_it_is_still_read(tmp_path):
    """A SKILL.md that is frontmatter and no body — the shape a skill starts life as."""
    (tmp_path / "SKILL.md").write_text("---\nname: d\ndescription: Does a thing.\n---")
    assert _skill_description(tmp_path / "SKILL.md") == "Does a thing."


def test_a_plain_multiline_description_counts_as_present(tmp_path):
    """`description:` with the text on indented continuation lines is ordinary YAML and loads fine
    in OpenCode. Reading only the first line would call it undescribed and log the loudest error in
    the function against a skill that is perfectly healthy."""
    (tmp_path / "SKILL.md").write_text(
        "---\nname: m\ndescription:\n  Does a thing,\n  at some length.\n---\n")
    assert _skill_description(tmp_path / "SKILL.md")


def test_a_block_description_counts_as_present(tmp_path):
    """`description: >-` carries its text on the lines below. Reading only the first line would
    call a perfectly good skill undescribed, which is a false alarm in the loudest place."""
    (tmp_path / "SKILL.md").write_text("---\nname: b\ndescription: >-\n  Does a thing.\n---\n")
    assert _skill_description(tmp_path / "SKILL.md")
