"""A generated app's tabs keep their panes, and what is in them, when the viewer switches (#681).

Tabs that lost their state on every switch were the app, not Ant Design: the vendored antd 5.11.2
keeps inactive panes mounted by default (`destroyInactiveTabPane` is false). What remounted them was
generated code: a pane rendered only while active, keys that changed between renders, a component
defined inside render. So the fix is an instruction, where the agent reads about Tabs and States.
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "template"


def _states(stack: str) -> str:
    text = (TEMPLATE / stack / "AGENTS.md").read_text()
    start = text.index("### States")
    return text[start:text.index("\n### ", start + 1)]


def test_the_antd_component_list_says_how_tabs_keep_their_panes():
    row = next(line for line in (TEMPLATE / "fastapi-antd/AGENTS.md").read_text().splitlines()
               if line.startswith("| `antd` |"))
    assert "`items`" in row and "conditionally" in row and "top level" in row


def test_the_antd_states_say_a_tab_pane_is_never_rendered_conditionally():
    states = _states("fastapi-antd")
    assert "Tabs" in states and "`items`" in states and "conditionally" in states


def test_the_react_states_hide_inactive_panels_rather_than_unmount_them():
    states = _states("react-vite")
    assert "hidden={active !== key}" in states and "top level" in states
