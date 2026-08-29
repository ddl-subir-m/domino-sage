"""The URL seeds the selected app once, then follows the server (#100).

WHAT WAS WRONG. `builder.js` re-asserted `?app=` whenever `activeApp` drifted away from it. That
reads as a tab holding its deep link, but `selectApp` WRITES: it posts the per-Project selection —
the one `WorkspaceManager.selected_app_id` resolves and `Project.app_for_turn` reads — and reloads
the whole of Build. So a tab was not holding a view, it was overwriting the app every other tab is
looking at, on the 30-second poll, for as long as it stayed open. Two tabs naming two apps traded
the selection back and forth for ever, each one's poll reading the other's write as drift.

WHAT WAS DECIDED. The server is authoritative; the URL seeds it and then follows it. The same
shape the resolution effect below it already had for the same reason — it leaves `activeApp` out of
its dependencies so that selecting the app it resolved cannot make it ask again.

WHY THE REWRITE IS HALF THE FIX. Server-wins on its own picks a winner and leaves the address bar
naming the loser, which is the disagreement this ticket is named after rather than a fix for it. So
a selection that moves under a tab takes the URL with it, through `replaceState` — following
somebody else's choice is not a place the Back button should return to. And the rewrite must not
come back round as a request: the tab notes the app it wrote itself, and the seed skips it.

WHAT IS DELIBERATELY NOT REWRITTEN. A link naming NO app. It disagrees with nothing, and pinning
the resolved app into it would take the resolution away from whoever opens the link next.

TWO STORES, NOT ONE. `js/route_selection_harness.mjs` runs each tab in its own vm context, because
the claim is about what two tabs do to each other THROUGH the server and one store cannot show it.
Its React shim honours effect dependencies, which is the one thing this fix turns on: "seeds once"
and "re-asserts for ever" are the same code with different dependency lists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "route_selection_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _two_tabs() -> dict:
    """Two tabs on one Project, naming two different apps, then three poll ticks each."""
    return _run(
        [{"tabs": ["#/build/thr_many?app=app_a", "#/build/thr_many?app=app_b"], "ticks": 3}]
    )[-1]


def _effects() -> list[tuple[str, str]]:
    """Every effect `BuildMode` schedules, as (body, dependency list).

    Read off the source because a dependency list is not observable from the outside: an effect
    that fires once and an effect that fires for ever look the same at the moment they first fire.
    """
    src = (_JS / "modes" / "builder.js").read_text()
    body = src[src.index("SW.BuildMode = function BuildMode(") :]
    found: list[tuple[str, str]] = []
    at = 0
    while (at := body.find("useEffect(", at)) != -1:
        start = body.index("{", at)
        depth = 0
        for end in range(start, len(body)):
            depth += {"{": 1, "}": -1}.get(body[end], 0)
            if depth == 0:
                break
        found.append((body[start : end + 1], body[end + 1 : body.index(");", end)]))
        at = end
    assert found, "BuildMode schedules no effects at all"
    return found


def _effect_calling(needle: str) -> tuple[str, str]:
    hits = [e for e in _effects() if needle in e[0]]
    assert len(hits) == 1, f"{needle} is in {len(hits)} of BuildMode's effects"
    return hits[0]


# ---- two tabs, one selection ---------------------------------------------------------------


@needs_node
def test_each_tab_seeds_the_selection_once_when_it_arrives():
    """A deep link still means what it meant: the app it names is the app you land on. One write
    each, because that is the arrival — the ticks after it are the part that must be free."""
    step = _two_tabs()
    assert step["seeded"]["writes"] == [
        "t1 POST /apps/app_a/select",
        "t2 POST /apps/app_b/select",
    ]
    assert [v["app"] for v in step["seeded"]["views"]] == ["app_a", "app_b"]


@needs_node
def test_the_ticks_write_nothing_at_all():
    """The whole of the defect, in one number. Six ticks across two tabs, and every one of them
    used to be an app switch: a POST, a `loadBuild`, and a selection moved for everybody in the
    Project. A settled pair reads and writes nothing."""
    assert _two_tabs()["tickWrites"] == []


@needs_node
def test_two_tabs_reach_a_steady_state_rather_than_trading_the_selection():
    """Asserted as a steady state rather than as an outcome: it is not enough that the selection
    stops moving on the tick this test happens to look at, because the ping-pong took a full round
    trip to show and any single tick could be caught mid-swap."""
    step = _two_tabs()
    assert [r["selected"] for r in step["rounds"]] == ["app_b", "app_b", "app_b"]
    for one in step["rounds"]:
        assert [v["app"] for v in one["views"]] == ["app_b", "app_b"]


@needs_node
def test_the_tab_that_lost_follows_the_server_rather_than_reverting_it():
    """`t1`'s URL names `app_a` and `t2` selected `app_b` after it. The server is authoritative,
    so `t1` shows `app_b` — the app whose transcript, Bindings and preview every other surface in
    that tab is already reading."""
    last = _two_tabs()["rounds"][-1]["views"][0]
    assert last["app"] == "app_b"
    assert last["name"] == "P&L report"


@needs_node
def test_the_address_bar_stops_naming_an_app_the_tab_is_not_showing():
    """The other half. Picking a winner and leaving the URL naming the loser is the disagreement
    this ticket is named after, not a fix for it."""
    assert [v["hash"] for v in _two_tabs()["views"]] == [
        "#/build/thr_many?app=app_b",
        "#/build/thr_many?app=app_b",
    ]


# ---- a selection moved by somebody else -----------------------------------------------------


def _moved() -> dict:
    """One tab, and `/apps` starts answering with a different selected app — which is exactly what
    another tab's write looks like from here. Two ticks, because "followed, not reverted" is a
    claim about the tick AFTER the one that moved it."""
    return _run([{"at": "#/build/thr_many?app=app_a", "moveTo": "app_c"}])[-1]


@needs_node
def test_a_selection_moved_elsewhere_is_followed_and_the_url_goes_with_it():
    step = _moved()
    assert step["before"]["app"] == "app_a"
    assert step["after"]["app"] == "app_c"
    assert step["after"]["hash"] == "#/build/thr_many?app=app_c"


@needs_node
def test_the_rewritten_url_does_not_ask_for_the_old_app_back():
    """The rewrite hands the seed effect a new `?app=`, which is the one way this fix could have
    re-entered itself. The tab knows it wrote that one, so nothing is selected on the way through
    and the second tick has nothing left to undo."""
    step = _moved()
    assert step["tickWrites"] == []
    # The reads a tick has always cost, and no `loadBuild` cascade behind them: following is not
    # an app switch, and the app-scoped lists moved with the selection under #95 already.
    assert step["calls"].count("t1 GET /apps") == 2


# ---- the paths that still write --------------------------------------------------------------


@needs_node
def test_picking_an_app_still_goes_through_the_route():
    """The one-writer rule (#78). The header's app list writes the ROUTE, and this effect is what
    turns that into a selection — a seed that stopped reading the URL would leave the control lit
    up over an app nobody had switched to."""
    step = _run(
        [{"at": "#/build/thr_many?app=app_a", "pick": "#/build/thr_many?app=app_d"}]
    )[-1]
    assert step["writes"] == ["t1 POST /apps/app_d/select"]
    assert step["after"]["app"] == "app_d"
    assert step["selected"] == "app_d"


@needs_node
def test_a_link_naming_no_app_resolves_one_and_is_not_pinned_to_it():
    """The effect below the seed, unchanged: a bare `#/build/<id>` still lands on the app the
    Conversation bound last. Nothing writes that answer into the URL — a bare link disagrees with
    nothing, and pinning it would take the resolution from whoever opens it next."""
    step = _run([{"bare": "#/build/thr_bound"}])[-1]
    assert step["settled"]["app"] == "app_c"
    assert step["settled"]["hash"] == "#/build/thr_bound"
    assert step["after"]["hash"] == "#/build/thr_bound"
    # Resolved once and selected once, and the ticks behind it ask for nothing.
    assert step["writes"] == ["t1 POST /apps/app_c/select"]
    assert step["tickWrites"] == []


@needs_node
def test_a_conversation_bound_to_nothing_leaves_the_selection_where_it_is():
    """`resolveConversationApp` answers nothing rather than guessing, which is what Build did
    before it existed. The tick must not turn that into a write either."""
    step = _run([{"bare": "#/build/thr_many"}])[-1]
    assert step["settled"]["app"] == "app_a"
    assert step["writes"] == []
    assert step["tickWrites"] == []


# ---- the dependency lists, which is where the difference lives -------------------------------


def test_the_seed_effect_does_not_depend_on_the_selected_app():
    """The latch, named. Firing on `activeApp` is what made the URL a standing instruction rather
    than a starting point, and it is invisible from the outside — the first fire looks identical
    either way."""
    body, deps = _effect_calling("SW.store.selectApp(appId)")
    assert deps.strip() == ", [appId]", deps
    assert "activeApp" not in body


def test_the_resolution_effect_still_fires_once():
    """Untouched on purpose. It made this call first, and making it depend on `activeApp` to reach
    the new rule would break the reason it gives for not depending on it."""
    _, deps = _effect_calling("resolveConversationApp")
    assert deps.strip() == ", [appId, conversationId]", deps


def test_the_url_follows_through_replace_state_rather_than_a_push():
    """Following somebody else's selection is not a place the Back button should return to, and
    `SW.router.replace` is the only thing in the Workbench that writes a URL without one."""
    body, _ = _effect_calling("SW.router.replace")
    assert "SW.router.go" not in body
    assert "replaceState" in (_JS / "router.js").read_text()


def test_the_rewrite_uses_the_one_route_grammar():
    """`SW.appRoute` is Build's route grammar and lives beside the router that reads it. A second
    copy of the template here would be a second place for `#/build?app=` to lose its conversation."""
    body, _ = _effect_calling("SW.router.replace")
    assert "SW.appRoute(" in body
    assert "#/build" not in body
