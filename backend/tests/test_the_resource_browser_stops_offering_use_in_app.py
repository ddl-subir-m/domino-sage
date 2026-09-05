"""The Resource Browser stops offering `Use in {app}` (#144, ADR-0021).

THE CONTRACT HALF. #141 opened the Build header's own door, #142 gave a Data Source a bind that
takes one argument, #143 re-pointed the two composer repairs at the app's surface. Each of those was
an EXPAND: they were all landed while the rail's copy was still live, so that no window ever existed
in which a Resource had no door at all. This ticket closes the window. `use-in-app` leaves
`js/components/resource-panel.js`, and the rail keeps only the acts whose scopes it owns or points
at.

WHY THE ACT HAD TO MOVE, and not merely be re-labelled. `Use in this chat` writes a chip on one
Conversation. `Use in {app}` writes a committed manifest that publish reads
([ADR-0010](../../docs/adr/0010-publish-reads-the-declaration-not-the-code.md)) and that a deployed
app depends on weeks after the Conversation is dead. ADR-0011 already made both labels name their
scopes; naming the scope never conveyed the weight, and ADR-0011 never claimed it did. ADR-0021
carries the weight in the separation instead — two acts of that different a cost are not adjacent
items in one menu in one style.

WHAT THIS FILE IS NOT. It is not a removal test. ADR-0021 EXTENDS ADR-0011's scope-naming rule from
removal to addition, so `test_removal_lands_in_the_in_this_app_section.py` and
`test_requirement_dies_and_removal_names_its_scope.py` are the regression signal for this ticket and
pass untouched. If either had to move, the rule would have been broken rather than extended.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

APP = "Rate curve viewer"
APP_ID = "app_c"


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _row(rows: list[dict], name: str) -> dict:
    """The one row whose name is `name`. No section to name: the panel is the Project's one list
    and the app's own is the App dependencies modal, so each list is passed in whole (#151)."""
    found = [r for r in rows if name in r["texts"]]
    assert len(found) == 1, f"{name} appears {len(found)} times: {rows}"
    return found[0]


def _keys(row: dict) -> list[str]:
    return [i["key"] for i in row["items"] if i.get("key")]


# ---- the door that closes ------------------------------------------------------------------------


@needs_node
def test_the_project_row_offers_no_act_that_adds_to_the_app():
    """Driven through the same step that asserted the act WORKED, so the two readings of this row
    are the same gesture and not two descriptions of it. `app_c` holds `al_2` only, so
    `llm_alias:al_1` is the one row the act ever existed for — the state where an absence could be
    a bug rather than a row that had nothing to offer anyway."""
    step = _run([{"useIn": "Claude Sonnet 4", "thread": "thr_many", "select": APP_ID}])[-1]
    assert step["item"] is None
    # And nothing reached the wire, which is the claim the label's absence alone cannot make: a menu
    # item is data on a prop, so a handler left behind a deleted label would still bind on a key.
    assert step["posted"] == []
    assert "POST /bindings" not in step["calls"]


@needs_node
def test_the_panel_cannot_reach_the_act_at_all():
    """Read as source, because the claim is about there being no copy. The label is one of two
    halves — `onMenu` dispatches on a KEY — and a handler that outlived its label is reachable from
    any other menu that ever grows the same key."""
    panel = (_JS / "components" / "resource-panel.js").read_text(encoding="utf-8")
    # The quoted key and the CALL, not the bare names. ADR-0014's rule is that prose and identifiers
    # are not the same string, and this file's comments have to be free to say where the act went.
    assert "'use-in-app'" not in panel
    assert "SW.store.bindToApp" not in panel
    # The flag that put the act on the row, asked of the two places an identifier can live — the
    # `const` and the prop — rather than of the whole text, because the comment that records where
    # the flag WENT still spells its name and ADR-0014's rule is that prose and identifiers are not
    # the same string. `saysAppUse` is the SIGN and stays: the two came apart in #129 precisely so
    # this one could go without taking the sign with it.
    assert "const canBind" not in panel
    assert "canBind," not in panel
    assert "saysAppUse" in panel


def test_every_route_that_binds_a_resource_is_on_the_apps_own_surface():
    """ADR-0021's rule, asked of the whole client rather than of the one file this ticket edits. The
    Build header's picker and the refusal card's repair both bind, and both are the app's own
    surface; the Chat handoff binds on the server (`_bind_from_handoff`), which is the ADR's named
    exception because the handoff IS the moment a person says the Conversation is becoming an app.
    Any other file reaching this act would be a fourth door on a surface that owns no such scope."""
    reaches = sorted(
        str(f.relative_to(_JS))
        for f in _JS.rglob("*.js")
        # The CALL and not the name, so that a file free to discuss the act is not read as taking it.
        if "store.bindToApp(" in f.read_text(encoding="utf-8")
    )
    assert reaches == ["modes/builder.js", "store.js"]


# ---- what the rail keeps -------------------------------------------------------------------------


@needs_node
def test_the_row_offers_no_menu_in_build_since_the_conversations_act_moved_to_chat():
    """The whole menu, asserted as a whole. Read as a list rather than as an absence, because the
    failure this ticket could cause is not the act coming back — it is the row losing the cheap act
    that shared the menu with it.

    `mention` used to be that act regardless of mode — this file's own name for it, before #147
    mode-gated `Use in this chat` to the surface with a Conversation to put it in
    (`test_use_in_this_chat_is_a_chat_only_act.py`). In Build the menu is empty now, and that is
    correct rather than a second door lost: #147 did not reopen anything this ticket closed."""
    row = _row(_run([{"panel": "thr_many", "select": APP_ID}])[-1]["rows"], "Claude Sonnet 4")
    assert _keys(row) == []
    assert [i["label"] for i in row["items"]] == []
    # The sign stays on the row the door left, which is the point of the #129 split: it is the only
    # thing that tells a Resource this app can reach from one merely sitting in the Project.
    assert f"Not used by {APP}" in row["texts"]


@needs_node
def test_a_resource_this_conversations_uses_offers_no_way_back_out_in_build():
    """The other half of the pair ADR-0015 named. `Stop using here` shared `Use in this chat`'s menu
    slot and its mode gate came with it (#147): both are acts on the Conversation, and Build has none
    to act on. The Chat-mode half of this pair is `test_use_in_this_chat_is_a_chat_only_act.py`."""
    row = _row(_run([{"panel": "thr_many", "select": APP_ID}])[-1]["rows"], "Market data EOD")
    assert _keys(row) == []
    assert [i["label"] for i in row["items"]] == []


@needs_node
def test_the_apps_own_list_keeps_the_removal_that_belongs_to_it():
    """ADR-0011's half of the rule, which this ticket extends and must not disturb. The app's own
    list owns the app scope, so the REMOVE is its and always was; what leaves is the ADD, which
    never had a home on any of these three lists. That list is the App dependencies modal now
    (#151) — the surface ADR-0021 already gave the app's Add and Scope doors."""
    row = _row(_run([{"panel": "thr_many", "select": APP_ID}])[-1]["appRows"], "Qwen 2.5")
    assert "remove" in _keys(row)
    assert f"Remove from {APP}" in [i["label"] for i in row["items"]]
    assert not any(i["label"].startswith(f"Use in {APP}") for i in row["items"])


@needs_node
def test_chat_is_unchanged_because_it_never_had_the_act():
    """A Binding names exactly one app and Chat shows none, so the act was already absent here
    (#127). Asked anyway, of every row and every menu, because "remove the app-scoped item" is a
    change that can reach the mode that had no app-scoped item to remove."""
    step = _run([{"panel": "thr_many", "select": APP_ID, "mode": "chat"}])[-1]
    assert not any(i["key"] == "use-in-app"
                   for r in step["rows"] for i in r.get("items") or [])
    assert not any("Not used by" in t for r in step["rows"] for t in r["texts"])
    row = _row(step["rows"], "Claude Sonnet 4")
    assert any(i["label"] == "Use in this chat" for i in row["items"])


# ---- the act still exists, one surface over ------------------------------------------------------


@needs_node
def test_the_act_moved_rather_than_died():
    """The reason the three expand tickets landed first. This is #141's own assertion, repeated from
    the ticket that closes the window, because "the rail no longer offers it" is only correct while
    somewhere else does — otherwise a Resource added to the Project after a plan crossed from Chat
    could never reach the app again, which is the bug #127 was written for."""
    step = _run([{
        "addIn": True, "thread": "thr_many", "select": APP_ID, "pick": "llm_alias:al_1",
    }])[-1]
    assert step["posted"] == [{"kind": "llm_alias", "id": "al_1"}]
    assert "llm_alias:al_1" in step["bindings"]
