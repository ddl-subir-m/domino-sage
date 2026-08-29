"""The Build header ships the selected app, and opens the one it shipped (#89).

TWO THINGS THIS TICKET INHERITED AS TRUE. #70 made a publish per Built App: the Domino App id
lives in the app's own `settings.json` (`Workspace.domino_app_id`), so `Orchestrator.publish`
ships the app Build is pointed at and cannot ship over a neighbour. That is what makes a control
in a header carrying an app selector safe to build at all — the hazard it was blocked on was pick
app B, press publish, ship over app A. And `_app_row` already carried `publishedAt`; what it did
not carry was anywhere to go, which is what `Open app` needs.

THE ROW CARRIES THE URL, NOT THE ID. The id still never leaves the server, which is the line
`published` was written on. The rewrite that turns an id into a page a browser can open —
`/apps-internal/{id}` 404s and `/modelproducts/{id}` does not — is control-plane knowledge that
lives in `_viewer_url` and has been re-learned from live Domino once; a browser holding the id
would be its second author. `/api/gallery` hands its cards a URL for the same reason.

ONE CONTROL, NOT A PAIR. #86 refused the design proposal's `Rebuild` and `Update`: describing a
change in the composer is what rebuilds an app, so a second control under a second word is a
second way to do one thing. The first publish and every one after are the same act on the same
object. What moves between them is the confirm's sentence, because the creator's question moves —
before the first, "where does this end up"; after it, "does the link I already sent people change".

TWO DOORS, NEVER ONE THAT MOVES. `Open app` opens the deployed App and the toolbar's control opens
the local preview. Two controls, two labels, two destinations, asserted by where each one went.

WHAT THIS TICKET DID NOT DO. The pre-publish notice was #35, which was blocked on this landing:
`GET /api/publish-check` existed and stayed uncalled, because a warning rendered here would have
been the next ticket built early and without its own criteria. #35 has since landed on the confirm
this ticket opened, so the last test below asserts that rather than its absence.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench"

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
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _publish(select: str, *, confirm: bool = True, refuse: str = "", moves_to: str = "",
             build_running: bool = False) -> dict:
    return _run([{"publish": "thr_many", "select": select, "confirm": confirm, "refuse": refuse,
                  "movesTo": moves_to, "buildRunning": build_running}])[-1]


def _open(select: str) -> dict:
    return _run([{"openapp": "thr_many", "select": select}])[-1]


def _published(step: dict) -> list[str]:
    return [a["id"] for a in step["apps"] if a["published"]]


# ---- one control, where the other app actions already live -------------------------------------


@needs_node
def test_the_header_offers_to_publish_the_app_it_names():
    """In the `…` beside the app name, on the shape #82 built and #38 ruled on: a text-labelled
    item, not an icon crowding the toolbar."""
    step = _publish("app_b", confirm=False)
    assert step["item"]["label"] == "Publish"
    assert step["item"]["disabled"] is False


@needs_node
def test_publishing_is_one_control_and_not_a_rebuild_update_pair():
    """#86's refusal, kept: a second control under a second word would be a second way to do the
    one thing the composer already does. Every item in the menu, so the pair cannot arrive as two
    keys that happen to read differently."""
    step = _publish("app_a", confirm=False)
    labels = [i["label"] for i in step["items"] if not i["divider"]]
    assert len([label for label in labels if "ublish" in label]) == 1, labels
    assert not any("Rebuild" in label or "Update" in label for label in labels), labels


# ---- it confirms, on #76's pattern -------------------------------------------------------------


@needs_node
def test_publishing_confirms_before_it_runs():
    """Consequential and outward-facing: the code leaves the builder and lands somewhere other
    people can open. Nothing is asked of the server until the question is answered."""
    step = _publish("app_b", confirm=False)
    assert step["confirm"] is not None
    assert "POST /publish" not in step["calls"], step["calls"]


@needs_node
def test_the_confirm_names_and_quotes_the_app():
    """The name is quoted for the reason Reset quotes it (`composer.js:228`): a display name starts
    as the title of the plan the app was built from, and those end in a full stop, which unquoted
    lands one in the middle of this question."""
    assert _publish("app_b", confirm=False)["confirm"]["title"] == "Publish “P&L report”?"


@needs_node
def test_the_confirm_says_what_publishing_does_not_touch():
    """#76's treatment. The fear on the way to this button is that the whole Project goes out, or
    that shipping one app takes the conversation it was built from with it."""
    said = _publish("app_b", confirm=False)["confirm"]["content"]
    assert "Only this app goes out" in said, said
    assert "other Built Apps" in said and "conversation" in said, said


@needs_node
def test_the_confirm_makes_no_claim_about_the_attached_files():
    """`public/data/` is gitignored, so the push does not carry the bytes and the deployed app
    rehydrates them from the manifest — which makes "your files are published" and "your files stay
    here" both wrong. A confirm is the last place to be approximately right."""
    said = _publish("app_b", confirm=False)["confirm"]["content"]
    assert "attach" not in said.lower(), said
    assert "file" not in said.lower(), said


@needs_node
def test_a_first_publish_says_where_the_app_ends_up():
    said = _publish("app_b", confirm=False)["confirm"]["content"]
    assert "URL of its own" in said, said
    # It must not promise a stable URL to somebody who has not got one yet.
    assert "doesn’t change" not in said, said


@needs_node
def test_a_republish_says_the_url_people_already_have_does_not_change():
    """The other side of the same act, and the creator's actual question once there is a link out
    in the world. One control, two sentences — the sentence is what moves."""
    step = _publish("app_a", confirm=False)
    assert step["confirm"]["title"] == "Publish a new version of “Desk dashboard”?"
    assert "doesn’t change" in step["confirm"]["content"], step["confirm"]["content"]


@needs_node
def test_the_confirm_is_not_dressed_as_a_destructive_one():
    """Delete and Reset are danger-styled because they throw work away. Publishing adds something,
    and borrowing the red would teach that the two are the same kind of act."""
    assert _publish("app_a", confirm=False)["confirm"]["danger"] is False


@needs_node
def test_cancelling_publishes_nothing():
    # The two GETs are #35's pre-publish notice, which is READ on the way into the question — a
    # notice that arrived only after you agreed would be a notice about something already done.
    # They take nothing and change nothing, and neither is a `POST /publish`, which is the claim.
    step = _publish("app_b", confirm=False)
    assert step["calls"] == ["GET /publish-check", "GET /publish-egress"], step["calls"]
    assert _published(step) == ["app_a", "app_d"]


# ---- it reaches the selected app and no other --------------------------------------------------


@needs_node
def test_publishing_ships_the_selected_app():
    """The load-bearing half: `SW.api.publish` was a stub returning `{}` and nothing called it, so
    `POST /api/publish` had been tested and never reached."""
    step = _publish("app_b")
    assert "POST /publish" in step["calls"], step["calls"]
    assert step["acted"] == "ok"


@needs_node
def test_publishing_ships_no_app_but_the_selected_one():
    """The hazard #70 closed, asserted from the near side. The request carries no app id — the
    server ships the app it has selected — so the claim is about what moved, not about a path."""
    step = _publish("app_b")
    assert _published(step) == ["app_a", "app_b", "app_d"]
    assert [a for a in step["apps"] if a["id"] == "app_c"] == [{"id": "app_c", "published": False, "url": ""}]


@needs_node
def test_a_publish_that_fails_still_re_reads_the_row():
    """A publish can fail AFTER it has succeeded: `record_domino_app` and `mark_published` are
    written before the response is built, so a 502 on the way out leaves a live Domino App
    recorded. Re-reading only on success would leave `Open app — publish it first` on an app that
    is already deployed, until something else happened to reload the list."""
    step = _publish("app_b", refuse="Domino answered 502.")
    assert step["calls"] == ["GET /publish-check", "GET /publish-egress",
                             "POST /publish", "GET /apps"], step["calls"]


@needs_node
def test_publishing_re_reads_the_row_rather_than_guessing_at_it():
    """Three things the row carries move on a publish — `published`, `publishedAt` and the URL
    `Open app` opens — and the header reads every one of them off the row. Patching it from the
    response would leave the fourth reader of that row to find out on the next tick."""
    step = _publish("app_b")
    # Written out rather than filtered down to the two that write: the two reads in front are #35's
    # pre-publish notice, and asking for either one twice is a regression this exactness catches.
    assert step["calls"] == ["GET /publish-check", "GET /publish-egress",
                             "POST /publish", "GET /apps"], step["calls"]
    assert [a["url"] for a in step["apps"] if a["id"] == "app_b"] == ["/modelproducts/da_b?scope=project"]


@needs_node
def test_a_published_app_can_be_opened_the_moment_it_is_published():
    """`Open app` was the item saying "publish it first" a moment ago. If the row did not move,
    the creator's next click would be on a control still telling them to do what they just did."""
    step = _publish("app_b")
    assert [i["label"] for i in step["items"] if i["key"] == "open"] == ["Open app"]


@needs_node
def test_the_success_message_says_the_app_is_not_up_yet():
    """A deploy is not finished when the call answers — Domino is still bringing the container up.
    Unsaid, the first click on `Open app` reads as an app that does not work."""
    assert "few minutes" in " ".join(_publish("app_b")["said"])


@needs_node
def test_a_publish_answered_under_another_app_publishes_nothing():
    """The request carries no app id — the server ships whatever it has selected — so a confirm
    left open while the selection moved would ship an app this modal never named. The 30-second
    app poll is enough to move it, and a modal sits open for as long as somebody leaves it there.
    The same capture-and-refuse the removal routes landed on for the same reason (ADR-0011)."""
    step = _publish("app_b", moves_to="app_a")
    assert "POST /publish" not in step["calls"], step["calls"]
    assert _published(step) == ["app_a", "app_d"]
    # It names both apps: which one it was going to publish, and which one you are looking at.
    said = " ".join(step["said"])
    assert "P&L report" in said and "Desk dashboard" in said, said


@needs_node
def test_a_void_question_takes_its_modal_with_it():
    """Unlike a refusal, which is worth reading with the modal still behind it. Pressing again
    would only earn the same sentence, because the app this confirm named is not the selected
    one any more."""
    assert _publish("app_b", moves_to="app_a")["openAfter"] is False


@needs_node
def test_a_refused_publish_holds_the_confirm_open_and_says_why():
    """Delete's precedent. Nothing was published, the app is exactly as it was, and the answer to
    a refusal is usually to read it and press this again — a modal that closed would take the
    sentence with it."""
    step = _publish("app_b", refuse="A Data Source this app queries has no Scope.")
    assert step["acted"] == "held open"
    assert "A Data Source this app queries has no Scope." in step["said"]
    assert _published(step) == ["app_a", "app_d"]


@needs_node
def test_publishing_is_refused_while_a_build_is_running_in_that_app():
    """Publishing commits and pushes the working tree, and a turn writing files into it has not
    finished writing them — this would deploy half an edit. It says why rather than going quiet,
    on the precedent of the composer's Reset (`composer.js`)."""
    step = _publish("app_c")
    assert step["item"]["disabled"] is True
    assert step["item"]["label"] == "Publish — wait for this build to finish"
    assert step["calls"] == [], step["calls"]


@needs_node
def test_a_build_streaming_into_another_app_stops_this_one_too():
    """The commit is the PROJECT's — one repo holds every Built App — so a turn streaming into
    app A is equally in the way when app B is the one selected, and app B's row cannot say so.
    `building` is the row's and per app; `buildRunning` is this tab's and catches what the row
    cannot name. The server refuses this as well, so the two agree."""
    step = _publish("app_b", build_running=True)
    assert step["item"]["disabled"] is True
    assert step["item"]["label"] == "Publish — wait for this build to finish"
    assert step["calls"] == [], step["calls"]


@needs_node
def test_an_idle_project_can_publish():
    """The guard is two signals, and neither of them fires on an app nobody is building."""
    assert _publish("app_b", confirm=False)["item"]["disabled"] is False


# ---- two doors ---------------------------------------------------------------------------------


@needs_node
def test_open_app_opens_the_published_app():
    step = _open("app_a")
    assert step["appOpened"] == ["/modelproducts/da_a?scope=project"]


@needs_node
def test_open_app_and_the_preview_control_are_two_controls_with_two_destinations():
    """Never one button that changes where it goes. The labels differ, and so does what each one
    actually opened — a control that swapped its destination would read identically otherwise."""
    step = _open("app_a")
    assert step["appOpened"] != step["previewOpened"]
    assert step["previewOpened"] == ["./preview/"]
    assert "Open preview in a new tab" in step["labels"]


@needs_node
def test_open_app_says_why_it_cannot_open_an_app_that_was_never_published():
    """Not hidden. A control that vanishes leaves the creator to work out on their own that there
    is nothing to open yet, which is the dead end Reset's disabled label avoids."""
    step = _open("app_b")
    assert step["item"]["disabled"] is True
    assert step["item"]["label"] == "Open app — publish it first"
    assert step["appOpened"] == []


@needs_node
def test_the_row_carries_the_url_and_not_the_domino_app_id():
    """The id stays on the server, which is the line `published` was written on: what the browser
    gets is a destination it can open, not a handle it could name a Domino App by. The rewrite
    that builds one from the other lives in `_viewer_url` and has been re-learned from live Domino
    once — the UI must not become its second author."""
    builder = (_WORKBENCH / "js" / "modes" / "builder.js").read_text()
    assert "activeApp.url" in builder
    for source in ("js/api.js", "js/store.js", "js/modes/builder.js"):
        text = (_WORKBENCH / source).read_text()
        assert "/modelproducts/" not in text, source
        assert "scope=project" not in text, source
        assert "dominoAppId" not in text, source


# ---- what this ticket did not do, and what came after it ----------------------------------------


@needs_node
def test_the_confirm_this_ticket_opened_is_where_the_pre_publish_notice_landed():
    """This was the placeholder that kept #35 from being built early: it asserted the confirm asked
    `GET /api/publish-check` nowhere, because a warning rendered here would have been the next
    ticket without its own criteria. #35 has landed, so the claim inverts — the confirm this ticket
    opened is exactly the surface the notice attaches to, and it asks BOTH reads.

    What survives is the shape, not the silence: the two reads are asked in parallel and neither is
    awaited before the confirm appears. What the notice then SAYS is #35's own file's business —
    `test_a_publish_says_what_leaves_domino.py`."""
    step = _publish("app_b")
    assert "GET /publish-check" in step["calls"], step["calls"]
    assert "GET /publish-egress" in step["calls"], step["calls"]
    # The confirm was already on screen with its own two paragraphs before either read answered.
    assert "Only this app goes out." in step["confirm"]["openedWith"]
