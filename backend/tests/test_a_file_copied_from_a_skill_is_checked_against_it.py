"""A file a build copied from a Project skill is checked against that skill's file (#682).

Live (2026-10-07): a skill can ship code — `deal-brief/dealDesk.js` — and the build copies it into
the app, but the copy is model-written and nothing stops a later turn editing it. After a Build
turn that finished and changed the tree, every file it wrote whose basename matches a file a
Project skill ships is compared with that file, the leading comment aside, and a difference is said
under the turn. Flag only: it is not fed to the model, and there is no repair.
"""
from __future__ import annotations

from pathlib import Path

from sage import extensions

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import (  # noqa: F401
    _no_waiting,
    _orch,
    _skip_planning,
)
from .test_a_failed_build_turn_stays_on_screen import UNREADABLE
from .test_a_partial_build_says_what_it_left_out import THREE_STEPS
from .test_build_conversation_return import run as render

SKILL_MD = "---\nname: {name}\ndescription: A test skill.\n---\nCopy the file unchanged.\n"
DEAL_DESK = (
    "// Deal desk approval ladder, ported from mcp-deal-desk.\n"
    "// Copy unchanged.\n"
    "function discountApproval(pct) {\n"
    "  return pct <= 10 ? 'AE' : 'RVP';\n"
    "}\n"
)


def _skill(orch, name: str, files: dict[str, str]) -> None:
    extensions.add(orch.project(start_preview=False).record.path, {
        "kind": "skill", "name": name, "files": {"SKILL.md": SKILL_MD.format(name=name), **files}})


def _build(tmp_path: Path, *turns: Turn, skills: dict[str, dict[str, str]]) -> list[dict]:
    orch, _ = _orch(tmp_path, turns=list(turns))
    _skip_planning(orch)
    for name, files in skills.items():
        _skill(orch, name, files)
    for n in range(len(turns)):
        list(orch.build_stream(f"change {n}", conversation="c1"))
    return orch.project(start_preview=False).app_for_turn().read_history("c1")


def _drift(history: list[dict]) -> list[dict]:
    return [{k: r[k] for k in ("type", "files", "message")}
            for r in history if r["type"] == "skill-copy-drift"]


def test_an_identical_copy_says_nothing(tmp_path: Path):
    history = _build(tmp_path, Turn(writes={"static/dealDesk.js": DEAL_DESK}),
                     skills={"deal-brief": {"dealDesk.js": DEAL_DESK}})
    assert _drift(history) == []


def test_a_copy_that_only_rewords_the_leading_comment_says_nothing(tmp_path: Path):
    reworded = DEAL_DESK.replace(
        "// Deal desk approval ladder, ported from mcp-deal-desk.\n// Copy unchanged.\n",
        "/* Copied from the deal-brief skill.\n   Do not edit. */\n").replace("\n", "\r\n")
    history = _build(tmp_path, Turn(writes={"static/dealDesk.js": reworded + "  \n"}),
                     skills={"deal-brief": {"dealDesk.js": DEAL_DESK}})
    assert _drift(history) == []


def test_a_copy_with_a_changed_line_is_named(tmp_path: Path):
    edited = DEAL_DESK.replace("pct <= 10", "pct <= 13")
    history = _build(tmp_path, Turn(writes={"static/dealDesk.js": edited}),
                     skills={"deal-brief": {"dealDesk.js": DEAL_DESK}})
    assert _drift(history) == [{
        "type": "skill-copy-drift",
        "files": [{"app": "static/dealDesk.js", "skill": "deal-brief", "skillFile": "dealDesk.js"}],
        "message": "static/dealDesk.js no longer matches dealDesk.js in the deal-brief skill."}]


def test_a_copy_this_turn_did_not_write_is_not_checked_again(tmp_path: Path):
    edited = DEAL_DESK.replace("pct <= 10", "pct <= 13")
    history = _build(tmp_path, Turn(writes={"static/dealDesk.js": edited}),
                     Turn(writes={"static/app.js": "// unrelated\n"}),
                     skills={"deal-brief": {"dealDesk.js": DEAL_DESK}})
    assert len(_drift(history)) == 1


def test_a_python_copy_ignores_its_leading_hash_comment(tmp_path: Path):
    body = "def ladder(pct):\n    return 'AE' if pct <= 10 else 'RVP'\n"
    history = _build(tmp_path, Turn(writes={"app/ladder.py": "# Copied from a skill.\n" + body}),
                     skills={"deal-brief": {"ladder.py": "# Ported from the server.\n" + body}})
    assert _drift(history) == []


def test_a_skill_with_only_markdown_is_not_compared(tmp_path: Path):
    history = _build(tmp_path, Turn(writes={"static/notes.md": "different\n"}),
                     skills={"deal-brief": {"notes.md": "notes\n"}})
    assert _drift(history) == []


def test_two_skills_shipping_one_basename_are_each_compared(tmp_path: Path):
    other = DEAL_DESK.replace("'RVP'", "'CRO'")
    history = _build(tmp_path, Turn(writes={"static/dealDesk.js": DEAL_DESK}),
                     skills={"deal-brief": {"dealDesk.js": DEAL_DESK},
                             "pricing": {"lib/dealDesk.js": other}})
    [row] = _drift(history)
    assert row["files"] == [
        {"app": "static/dealDesk.js", "skill": "pricing", "skillFile": "lib/dealDesk.js"}]


def test_an_approved_build_is_checked_too(tmp_path: Path):
    edited = DEAL_DESK.replace("pct <= 10", "pct <= 13")
    orch, _ = _orch(tmp_path, turns=[THREE_STEPS, Turn(writes={"static/dealDesk.js": edited})])
    _skill(orch, "deal-brief", {"dealDesk.js": DEAL_DESK})
    list(orch.build_stream("build me a pipeline dashboard", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))
    history = orch.project(start_preview=False).app_for_turn().read_history("c1")
    assert [r["files"][0]["app"] for r in _drift(history)] == ["static/dealDesk.js"]


def test_a_turn_that_gave_up_is_not_checked(tmp_path: Path):
    edited = DEAL_DESK.replace("pct <= 10", "pct <= 13")
    history = _build(tmp_path, Turn(writes={"static/dealDesk.js": edited}, error=UNREADABLE),
                     skills={"deal-brief": {"dealDesk.js": DEAL_DESK}})
    assert _drift(history) == []


def test_the_transcript_draws_it_as_a_warning():
    rows = render({"savedHistory": [
        {"type": "user", "text": "Change it."},
        {"type": "done", "ok": True, "decision": "clean"},
        {"type": "skill-copy-drift", "files": [],
         "message": "static/dealDesk.js no longer matches dealDesk.js in the deal-brief skill."},
    ]})
    assert rows[-1] == {"type": "status", "ok": None, "warn": True, "value": (
        "static/dealDesk.js no longer matches dealDesk.js in the deal-brief skill.")}
