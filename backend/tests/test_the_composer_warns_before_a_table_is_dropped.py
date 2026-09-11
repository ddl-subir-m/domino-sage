"""The compose-time half of the Scope gap (#136's rule, applied to the table dimension).

WHAT WAS MISSING. Two lists answer "which tables can this app be pointed at". The `@` menu offers
the tables PINNED on the Project's row (`pinRow`); a turn honours the ones inside the selected app's
Scope. They are not the same list, and the warning between them read neither — `unusableMentions`
keyed on `kind:id` and stopped at "is the Resource bound", which a sibling table passes.

So the menu offered a table, the person picked it, the turn dropped it, and nothing said so at
either end. The build then answered from the app's OWN table and reported itself clean.

WHAT THIS ASSERTS. The warning names the Scope and the table it refused, its button opens the door
that owns the second act rather than binding a Resource the app already holds, and the mention still
goes out — this warns, it does not block, which is the rule every line of that guard lives by.

The quiet case is asserted beside it, because a warning that fires on the bound table would fire on
nearly every Build turn, and noise is the failure this shape is most likely to fail into.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "table_scope_guard_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH (it is in the Sage image)"
)

# The app is bound to `DWH.MARTS.FCT_USAGE_DAILY`. `DIM_ACCOUNT` is its sibling, pinned on the
# Project's row and therefore in the menu.
REFUSED = "Add a chart of @DIM_ACCOUNT by segment"
BOUND = "Add a chart of @FCT_USAGE_DAILY by day"


def _run(prompt: str, unscoped: bool = False) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"prompt": prompt, "unscoped": unscoped}),
        capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_menu_offers_a_table_the_scope_does_not_reach():
    """The premise, pinned so the rest cannot quietly stop being about anything. The menu draws the
    Project's pins and has no app Scope to filter them by — which is the gap, not a bug in the menu:
    a Project's pins are what a person is choosing between, and the app's Scope is editable."""
    assert _run(REFUSED)["menu"] == ["DIM_ACCOUNT", "FCT_USAGE_DAILY"]


@needs_node
def test_the_warning_names_what_the_app_reads_instead():
    """Both halves in one clause. A line that said only "couldn't use this" leaves the reader to
    guess why a table sitting in their own menu is out of reach."""
    line = _run(REFUSED)["guard"]["line"]

    assert "@DIM_ACCOUNT" in line
    assert "DWH.MARTS.FCT_USAGE_DAILY" in line
    assert "Snowflake-Data-Warehouse" in line, "two bound stores can each hold a table of one name"
    assert "doesn't use" not in line, "the app HAS this store — that sentence is the other gap's"


@needs_node
def test_the_button_opens_the_scope_and_binds_nothing():
    """The Binding is already on disk, so a bind would rewrite the record with the values it holds
    and leave the gap exactly where it was.

    And it opens rather than finishes, unlike the bind fixes beside it: widening a Scope has a shape
    — the schema, or this table instead of the bound one — and the app's screens read the bound
    table, so a click that moved the Scope off it would break them to satisfy one prompt."""
    step = _run(REFUSED)

    assert step["guard"]["labels"] == ["Choose what Usage dashboard reads"]
    assert step["scopeOpened"] == ["data_source:ds-dwh"]
    assert step["sentAfterClick"] == 0, "this act opens a door; it does not send the turn behind it"


@needs_node
def test_the_warning_never_blocks_the_send():
    """It is a warning and not a gate. The mention goes out carrying the table, and the turn's own
    sentence is the backstop — which is why the two are built from one shape."""
    sent = _run(REFUSED)["sent"]

    assert sent[0]["resources"] == [{
        "kind": "data_source", "id": "ds-dwh", "name": "DIM_ACCOUNT",
        "table": "DIM_ACCOUNT", "sourceName": "Snowflake-Data-Warehouse"}]


@needs_node
def test_a_store_with_no_scope_yet_is_the_same_gap_in_its_first_state():
    """The ordinary way in: the header's picker binds a Data Source in one argument and leaves the
    Scope as a second act (#142), so a store bound there reaches no table at all until somebody
    answers it. `scopeShown` is "" for that, and "reads  inside Warehouse" is the line it would
    otherwise have drawn — the same button closes it, because it is the same missing act."""
    step = _run(REFUSED, unscoped=True)

    assert step["guard"]["line"] == (
        "Usage dashboard hasn't chosen what it reads inside Snowflake-Data-Warehouse.")
    assert step["guard"]["labels"] == ["Choose what Usage dashboard reads"]
    assert step["scopeOpened"] == ["data_source:ds-dwh"]


@needs_node
def test_the_bound_table_draws_no_warning():
    """The case that must stay silent, and the one this shape is most likely to get wrong."""
    step = _run(BOUND)

    assert step["guard"]["line"] == ""
    assert step["guard"]["labels"] == []


def test_the_store_travels_beside_the_token_and_never_instead_of_it():
    """`name` is the word that was typed and every sentence quotes it back, so a table row's `name`
    stays the TABLE's. The store rides alongside, which is what lets the refusal name the thing the
    button would actually record (`_unusable_mentions`)."""
    store = (_JS / "store.js").read_text()

    assert "if (ref.table && row.subtitle) ref.sourceName = row.subtitle;" in store
    assert "name: row.name || ''" in store


def test_one_map_still_picks_the_act_by_the_field_and_not_by_the_kind():
    """A Data Source is two states here — not bound at all, and bound too narrowly — and only one
    of them wants the bind. Keyed on kind, `data_source` would have to mean two acts at once."""
    store = (_JS / "store.js").read_text()

    assert "entry.table ? scope : MENTION_FIX[entry.kind]" in store
    assert "entry.table ? scoped : HINT[entry.kind]" in store, (
        "the replayed card's no-button branch has to split on the same field, or it tells the "
        "reader to add a Resource the app already holds")
