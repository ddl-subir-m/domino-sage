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


# ---- the rail click, which is what makes the ADR's rule reachable (#139) ----------------------
#
# ADR-0009 already said a Build link naming no app resolves the Conversation's newest bound
# handoff, and the test above proves the resolution works. It never happened on a click, because
# the rail stamped the selected app into every link it built — so the "names no app" case only
# ever arrived from a bookmark. The rail stops stamping, and the answer it already holds on the
# row moves the selection in the same beat as the navigation.


def _acts(*acts: dict, at: str = "#/build/thr_many?app=app_a") -> list[dict]:
    """Acts against ONE tab. "Regardless of what the header did earlier" is a claim about a
    session, and every step that mounts its own tab has thrown that session away."""
    return _run([{"at": at, "sequence": list(acts)}])[-1]["acts"]


@needs_node
def test_clicking_a_conversation_moves_build_to_the_app_it_bound():
    """The whole ticket in one act. The preview, the Build header and the panel's app section all
    read `activeApp` and are assigned together (#95), so the app moving IS the three of them
    moving — and the link the click produced names no app, which is the condition ADR-0009's rule
    needs and never got."""
    (clicked,) = _acts({"click": "thr_bound"})
    assert clicked["view"]["app"] == "app_c"
    assert clicked["view"]["thread"] == "thr_bound"
    assert clicked["view"]["hash"] == "#/build/thr_bound"
    assert clicked["writes"] == ["t1 POST /apps/app_c/select"]


@needs_node
def test_a_conversation_that_bound_several_lands_on_the_one_it_bound_last():
    """`thr_twice` handed off to `app_b` and then to `app_d`, and its tags name them in that
    order. A follow that read the tags would land on the first one; the answer is the handoff
    record, which the server has already reduced to the newest bound entry."""
    (clicked,) = _acts({"click": "thr_twice"})
    assert clicked["view"]["app"] == "app_d"
    assert clicked["writes"] == ["t1 POST /apps/app_d/select"]


@needs_node
def test_a_conversation_that_bound_nothing_leaves_the_selection_where_it_is():
    """The old rail could not tell "this Conversation's app" from "the app in front of me" — it
    stamped the second into the link and called it the first. Dropping the stamp must not turn
    into blanking: a Conversation with nothing to say about an app says nothing."""
    (clicked,) = _acts({"click": "thr_many"}, at="#/build/thr_bound?app=app_a")
    assert clicked["view"]["thread"] == "thr_many"
    assert clicked["view"]["app"] == "app_a"
    assert clicked["view"]["hash"] == "#/build/thr_many"
    assert clicked["writes"] == []


@needs_node
def test_the_conversation_is_never_drawn_beside_an_app_it_did_not_bind():
    """The flicker, named. Resolving over the network puts a frame on screen where the new
    Conversation's transcript sits beside the app you came from — the app card, the Bindings and
    the preview all describing work this Conversation never did — and then swaps it a round trip
    later. The answer is on the row the rail is already holding, so there is no such frame.

    Asserted on the frames rather than on the settled view, because the settled view is right
    either way and is exactly what a flicker hides behind."""
    (clicked,) = _acts({"click": "thr_bound"})
    assert clicked["trail"], "the click painted nothing at all"
    for frame in clicked["trail"]:
        if frame["thread"] == "thr_bound":
            assert frame["app"] == "app_c", clicked["trail"]
    # And the first thing the click asked the server for is the selection itself: a lookup ahead
    # of it would be the round trip the frame above is spent waiting for.
    assert clicked["calls"][0] == "t1 POST /apps/app_c/select", clicked["calls"]


@needs_node
def test_a_rail_click_names_no_app_however_the_header_was_used_before_it():
    """The stamp read `activeApp`, so switching app in the header once poisoned every rail link
    for the rest of the session — and the case ADR-0009 decided never arose."""
    picked, clicked = _acts(
        {"pick": "#/build/thr_many?app=app_d"},
        {"click": "thr_bound"},
    )
    assert picked["view"]["hash"] == "#/build/thr_many?app=app_d"
    assert clicked["view"]["hash"] == "#/build/thr_bound"
    assert clicked["view"]["app"] == "app_c"


@needs_node
def test_a_shared_link_still_beats_the_conversations_own_binding():
    """`?app=` stays readable grammar. `thr_bound` bound `app_c`, and a link that names `app_a`
    lands on `app_a` — a shared link means what it says, or it is not worth sharing."""
    step = _run([{"at": "#/build/thr_bound?app=app_a", "sequence": []}])[-1]
    assert step["acts"] == []
    seeded = _run([{"tabs": ["#/build/thr_bound?app=app_a"], "ticks": 1}])[-1]
    assert [v["app"] for v in seeded["views"]] == ["app_a"]
    assert seeded["views"][0]["hash"] == "#/build/thr_bound?app=app_a"


@needs_node
def test_an_app_started_inside_build_is_still_resolved_the_slow_way():
    """The fallback the ticket keeps. `thr_infield` never handed off (#74), so no entry names its
    app and the list has nothing to carry — its build turns are the only record, and reading them
    costs the round trip the common path no longer pays."""
    (clicked,) = _acts({"click": "thr_infield"})
    assert clicked["view"]["app"] == "app_d"
    assert clicked["writes"] == ["t1 POST /apps/app_d/select"]
    assert "t1 GET /threads/thr_infield/conversation" in clicked["calls"]


def test_the_rail_stops_stamping_the_selected_app_into_its_links():
    """The comment that said the opposite went with it. "Which app Build has in the preview is a
    view parameter, so it survives moving between conversations" contradicted an accepted ADR,
    and a reader who found it first would repair this back."""
    rail = (_JS / "components" / "conversation-list.js").read_text()
    route = rail[rail.index("SW.conversationRoute ="):rail.index("SW.openConversation =")]
    assert "activeApp" not in route, route
    assert "?app=" not in route, route
    assert "survives" not in rail
    assert "boundAppId" in rail


def test_the_resolver_asks_the_list_before_it_asks_the_server():
    """What makes the network call a correction rather than the common path. The answer is on the
    thread list the rail is drawn from, so a bare link opened beside a loaded rail resolves
    without asking — and the reads below it stay for the Conversations the list cannot answer."""
    src = (_JS / "store.js").read_text()
    body = src[src.index("async resolveConversationApp("):]
    body = body[:body.index("\n    async ")]
    assert body.index("boundAppId") < body.index("SW.api.thread("), body
    assert "SW.api.conversation(" in body


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


def test_picking_the_app_a_rewrite_once_named_still_selects_it():
    """The guard that stops a followed rewrite asking for its own app back is a ref, and a ref
    survives renders. Every other test here mounts its own tab, so each gets a fresh one.

    Drive three acts against ONE tab: follow the server to `app_b`, which records `app_b`; pick
    `app_a`; then pick `app_b` back. If that record were still `app_b` the last click would be
    swallowed as the rewrite it is not, the selection would never move, and the URL would snap back
    to the app the server still has — a picker click doing nothing, which is #100's disagreement
    reached from the other side."""
    step = _run([{"at": "#/build/thr_many?app=app_a", "sequence": [
        {"moveTo": "app_b"},
        {"pick": "#/build/thr_many?app=app_a"},
        {"pick": "#/build/thr_many?app=app_b"},
    ]}])[-1]

    followed, picked_away, picked_back = step["acts"]
    assert followed["writes"] == [], "following the server must not write the selection back"
    assert picked_away["writes"] == ["t1 POST /apps/app_a/select"]
    assert picked_back["writes"] == ["t1 POST /apps/app_b/select"], step["acts"]
    assert picked_back["view"]["hash"].endswith("app=app_b")
