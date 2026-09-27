"""A Build tab's app is the one its URL names.

Two tabs naming two apps used to trade the project selection: each poll read the other tab's
`POST /apps/{id}/select` as drift and wrote it back. A tab now keeps the app in `?app=`. `selectApp`
sets that app in this tab and does not post select. Create, delete, and handoff still select, and
that remains the default a URL with no app lands on. A poll refreshes the row; it does not install
the other tab's app, and it does not rewrite this tab's `?app=`.
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


@needs_node
@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_pending_app_selection_cannot_rewrite_the_route_or_old_app_preference(outcome):
    result = _run([{"delayedSelection": outcome}])[-1]
    assert result["pending"]["view"]["hash"] == "#/build/thr_twice?app=app_d"
    assert result["pending"]["prefs"]["project"]["app_a"] == "thr_many"
    assert result["after"]["selecting"] is None
    assert result["after"]["prefs"]["project"]["app_a"] == "thr_many"
    if outcome == "success":
        assert result["after"]["view"]["app"] == "app_d"
    else:
        # The URL already names the app the click asked for. A reload that fails does not put the
        # address bar back on the app this tab left.
        assert result["after"]["view"]["app"] == "app_d"
        assert result["after"]["view"]["hash"].endswith("?app=app_d")


@needs_node
def test_an_older_selection_of_the_same_app_cannot_clear_the_latest_request():
    assert _run([{"selectionABA": True}])[-1] == {
        "afterFirst": "app_b", "afterMiddle": "app_b", "final": None}


@needs_node
def test_a_deliberate_conversation_choice_after_following_an_app_is_remembered():
    assert _run([{"followThenChoose": True}])[-1]["project"]["app_b"] == "thr_twice"


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
def test_two_tabs_keep_the_app_their_url_names():
    """Tab A is on app A. Tab B's next bindings read still names app B, and neither poll rewrites
    the other's `?app=`."""
    step = _two_tabs()
    assert [v["hash"] for v in step["views"]] == [
        "#/build/thr_many?app=app_a",
        "#/build/thr_many?app=app_b",
    ]
    named = [c for c in step["calls"] if c.startswith("t2 ") and "GET /bindings" in c]
    assert named, step["calls"]
    assert named[-1].endswith(" app=app_b"), named


@needs_node
def test_each_tab_seeds_the_selection_once_when_it_arrives():
    """A deep link still means what it meant: the app it names is the app you land on. It does not
    post the project selection — that write is what the other tab's poll used to follow."""
    step = _two_tabs()
    assert step["seeded"]["writes"] == []
    assert [v["app"] for v in step["seeded"]["views"]] == ["app_a", "app_b"]


@needs_node
def test_the_ticks_write_nothing_at_all():
    """The whole of the defect, in one number. Six ticks across two tabs, and every one of them
    used to be an app switch: a POST, a `loadBuild`, and a selection moved for everybody in the
    Project. A settled pair reads and writes nothing."""
    assert _two_tabs()["tickWrites"] == []


@needs_node
def test_two_tabs_reach_a_steady_state_rather_than_trading_the_selection():
    """Each tab stays on the app its URL names. The project selection does not move, because neither
    tab posted one."""
    step = _two_tabs()
    assert [r["selected"] for r in step["rounds"]] == ["app_a", "app_a", "app_a"]
    for one in step["rounds"]:
        assert [v["app"] for v in one["views"]] == ["app_a", "app_b"]


@needs_node
def test_the_tab_that_lost_follows_the_server_rather_than_reverting_it():
    """`t1`'s URL names `app_a` and `t2` is showing `app_b`. `t1` keeps `app_a` — the app its
    transcript, Bindings and preview are reading."""
    last = _two_tabs()["rounds"][-1]["views"][0]
    assert last["app"] == "app_a"
    assert last["name"] == "Desk dashboard"


@needs_node
def test_the_address_bar_stops_naming_an_app_the_tab_is_not_showing():
    """Neither poll rewrites the other's `?app=`."""
    assert [v["hash"] for v in _two_tabs()["views"]] == [
        "#/build/thr_many?app=app_a",
        "#/build/thr_many?app=app_b",
    ]


# ---- a selection moved by somebody else -----------------------------------------------------


def _moved() -> dict:
    """One tab, and `/apps` starts answering with a different selected app — which is exactly what
    another tab's write looks like from here. Two ticks, because "followed, not reverted" is a
    claim about the tick AFTER the one that moved it."""
    return _run([{"at": "#/build/thr_many?app=app_a", "moveTo": "app_c"}])[-1]


@needs_node
def test_a_selection_moved_elsewhere_leaves_this_tab_where_it_is():
    step = _moved()
    assert step["before"]["app"] == "app_a"
    assert step["after"]["app"] == "app_a"
    assert step["after"]["hash"] == "#/build/thr_many?app=app_a"


@needs_node
def test_a_poll_after_somebody_else_moved_does_not_ask_for_the_old_app_back():
    """The tab did not follow, so it has nothing to undo. The reads a tick has always cost, and no
    `loadBuild` behind them."""
    step = _moved()
    assert step["tickWrites"] == []
    assert sum(c.startswith("t1 GET /apps") for c in step["calls"]) == 2


# ---- the paths that still write --------------------------------------------------------------


@needs_node
def test_picking_an_app_still_goes_through_the_route():
    """The header's app list writes the ROUTE, and the seed effect turns that into this tab's app.
    It does not post the project selection."""
    step = _run(
        [{"at": "#/build/thr_many?app=app_a", "pick": "#/build/thr_many?app=app_d"}]
    )[-1]
    assert step["writes"] == []
    assert step["after"]["app"] == "app_d"
    assert step["selected"] == "app_a"


@needs_node
def test_a_link_naming_no_app_resolves_one_and_is_not_pinned_to_it():
    """The effect below the seed, unchanged: a bare `#/build/<id>` still lands on the app the
    Conversation bound last. Nothing writes that answer into the URL — a bare link disagrees with
    nothing, and pinning it would take the resolution from whoever opens it next."""
    step = _run([{"bare": "#/build/thr_bound"}])[-1]
    assert step["settled"]["app"] == "app_c"
    assert step["settled"]["hash"] == "#/build/thr_bound"
    assert step["after"]["hash"] == "#/build/thr_bound"
    assert step["writes"] == []
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
    assert clicked["writes"] == []


@needs_node
def test_a_conversation_that_bound_several_lands_on_the_one_it_bound_last():
    """`thr_twice` handed off to `app_b` and then to `app_d`, and its tags name them in that
    order. A follow that read the tags would land on the first one; the answer is the handoff
    record, which the server has already reduced to the newest bound entry."""
    (clicked,) = _acts({"click": "thr_twice"})
    assert clicked["view"]["app"] == "app_d"
    assert clicked["writes"] == []


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
    bound = [f for f in clicked["trail"] if f["thread"] == "thr_bound"]
    assert bound, "no frame ever showed the Conversation that was clicked"
    for frame in bound:
        assert frame["app"] == "app_c", clicked["trail"]
    # And the first thing the click asked the server for is the selection itself: a lookup ahead
    # of it would be the round trip the frame above is spent waiting for.
    assert clicked["calls"][0].startswith("t1 GET /project"), clicked["calls"]
    assert not any(c.endswith("/select") for c in clicked["calls"])


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
    assert clicked["writes"] == []
    assert any(c.startswith("t1 GET /threads/thr_infield/conversation") for c in clicked["calls"])


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


def test_the_tab_does_not_rewrite_its_url_to_follow_another_app():
    """The effect that rewrote `?app=` when `activeApp` drifted is gone. The seed effect remains."""
    src = (_JS / "modes" / "builder.js").read_text()
    body = src[src.index("SW.BuildMode = function BuildMode("):]
    assert "followed.current" not in body
    assert "SW.router.replace(SW.appRoute(activeApp))" not in body
    _, deps = _effect_calling("SW.store.selectApp(appId)")
    assert deps.strip() == ", [appId]", deps


def test_picking_an_app_the_other_tab_selected_still_selects_it_here():
    """Another tab's selection does not move this one. Picking the app that tab selected still
    shows it here, and still does not post the project selection."""
    step = _run([{"at": "#/build/thr_many?app=app_a", "sequence": [
        {"moveTo": "app_b"},
        {"pick": "#/build/thr_many?app=app_a"},
        {"pick": "#/build/thr_many?app=app_b"},
    ]}])[-1]

    followed, picked_away, picked_back = step["acts"]
    assert followed["writes"] == []
    assert followed["view"]["app"] == "app_a"
    assert followed["view"]["hash"].endswith("app=app_a")
    assert picked_away["writes"] == []
    assert picked_back["writes"] == []
    assert picked_back["view"]["app"] == "app_b"
    assert picked_back["view"]["hash"].endswith("app=app_b")


@needs_node
def test_deleting_the_app_on_screen_moves_the_tab_onto_one_that_is_left():
    """The reload after a delete used to go out naming the deleted app in `X-Sage-App`, and the
    server refuses an app it no longer has — `/apps` too. The tab read that as a Project with no
    apps, kept pointing at the one just deleted, and New app answered `unknown app`."""
    step = _run([{"deleteShown": True}])[-1]

    after_delete = step["calls"][1:]
    assert not [c for c in after_delete if c.endswith("app=app_a")], after_delete
    assert step["after"]["apps"] == ["app_b", "app_c", "app_d"]
    assert step["after"]["view"]["app"] == "app_d"
    assert step["after"]["view"]["hash"].endswith("?app=app_d")
    assert step["create"] == "ok"
