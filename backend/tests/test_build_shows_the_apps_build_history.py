"""Build shows the selected Built App's build history (#88).

#86 settled what History is a history OF: the app's builds. The Conversation reading is already
taken — #56 made the merged transcript that — so this is the other one, and it answers a question
the transcript cannot: what has been built into THIS app, by whoever asked, in whichever
Conversation. A Conversation can drive several apps (#72) and several conversations can drive one,
so nothing here filters by conversation.

THE SERVER ALREADY ANSWERED IT. `GET /api/project/history` with no conversation named returns the
selected app's whole log and always has — its own docstring says so, and `Orchestrator.history`
reads `app_workspace(...)`, which is the SELECTED app's directory (ADR-0008). No caller ever asked
it that way: `api.js` passed a conversation on every call. So the first two tests below are about
the route as it stands, and the rest are about the frontend that now asks it.

THE HAZARD IS #101'S, THROUGH A NEW DOOR. The route carries no app id, so its answer is only ever
"whichever app was selected when it was asked". A read that resolves after the creator has moved to
another app is describing an app that is no longer on screen, and painting it is the wrong pairing
#95 fixed and #101 sequenced. So the history is a fifth app-scoped field and goes through the same
gate — `test_app_scoped_writes_are_sequenced.py` holds the gate itself, and the late-response race
below holds this field's use of it.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench"
_DRAWER = _WORKBENCH / "js" / "components" / "build-history.js"
_STORE = _WORKBENCH / "js" / "store.js"
_API = _WORKBENCH / "js" / "api.js"

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


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: no build runs here
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
    )


# ---- criterion 1, the server half --------------------------------------------------------------
#
# The route needed no change, and these two say what it already does rather than taking the
# docstring's word for it.


def test_the_unnamed_read_returns_the_selected_apps_whole_log(tmp_path: Path, monkeypatch):
    """Every build of the app, not one conversation's. A Conversation can drive several apps and a
    Built App can be driven by several conversations (#72), so a history filtered to the open one
    would leave out the builds that made the app what it is."""
    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    ws = orch._wm.app_workspace("Sage")
    ws.append_history({"type": "user", "text": "add a margin column"}, "thr_a")
    ws.append_history({"type": "done", "ok": True}, "thr_a")
    ws.append_history({"type": "user", "text": "sort by P&L"}, "thr_b")
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    rows = client.get("/api/project/history").json()["history"]

    assert [r["text"] for r in rows if r["type"] == "user"] == ["add a margin column", "sort by P&L"]
    # Both conversations, which is the whole point of naming none.
    assert {r["conversation"] for r in rows} == {"thr_a", "thr_b"}


def test_the_unnamed_read_is_never_another_apps_log(tmp_path: Path, monkeypatch):
    """The log lives in the app's own directory (ADR-0008), so "the selected app's" is settled by
    which directory is read rather than by a filter. Selecting the other app moves the answer."""
    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    first = orch._wm.selected_app_id()
    orch._wm.app_workspace("Sage", first).append_history({"type": "user", "text": "desk table"})
    second = orch.create_app()["id"]
    orch._wm.app_workspace("Sage", second).append_history({"type": "user", "text": "rate curve"})
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    orch.select_app(first)
    assert [r["text"] for r in client.get("/api/project/history").json()["history"]] == ["desk table"]
    orch.select_app(second)
    assert [r["text"] for r in client.get("/api/project/history").json()["history"]] == ["rate curve"]


# ---- criterion 1, the frontend half ------------------------------------------------------------


def _opened(select: str = "app_a", thread: str = "thr_many") -> dict:
    """The drawer opened from the Build header, over `app_a` — two runs, in two different
    conversations, one of them stamped and one not."""
    return _run([{"history": thread, "select": select}])[-1]


@needs_node
def test_the_header_opens_the_selected_apps_build_history():
    """Found by its label rather than by a key, so a control that stopped being in the header would
    not be found at all. The read it makes names no conversation: that is the whole difference
    between this list and the transcript behind it."""
    step = _opened()
    assert step["control"]["texts"] == ["Build history"]
    assert "GET /project/history" in step["calls"]
    assert step["drawer"]["title"] == "Build history · Desk dashboard"


@needs_node
def test_it_lists_one_entry_per_build_headed_by_the_prompt_that_asked_for_it():
    """Runs, not raw events. The app's log holds seven rows here and two builds, and the turns of
    each are behind a control rather than listed as builds of their own."""
    step = _opened()
    assert step["drawer"]["runs"] == 2
    # Newest first: a transcript is read forwards, a history is opened to see what happened last.
    assert step["drawer"]["prompts"] == ["Sort the desks by P&L", "Add a margin column"]
    assert step["drawer"]["folds"] == ["Show the 2 turns", "Show the 2 turns"]
    # Each entry told apart from the next. `order` is stamped by the MERGED read (#56) and a log
    # read straight off the app's disk has never carried one, so a list keyed on it would hand
    # every build the same key and React would draw the two as one.
    assert step["drawer"]["keys"] == ["run_3", "run_0"]
    assert len(set(step["drawer"]["keys"])) == step["drawer"]["runs"]


@needs_node
def test_a_row_that_belongs_to_no_build_is_not_listed_as_one():
    """A confirmed handoff writes its plan card into the log with no user row above it, and
    `app-reset` rows are appended outside any turn. The transcript draws them in place; a LIST OF
    BUILDS has nothing to list them as, so the fixture's loose `plan-proposed` is not an entry."""
    step = _opened()
    assert step["drawer"]["runs"] == 2
    assert "# Desk dashboard" not in " ".join(step["drawer"]["words"])


@needs_node
def test_a_build_with_no_stamp_shows_no_time():
    """Stamping was added recently, so older rows carry no `at`. The row shows no time rather than
    one derived from its neighbours — the rule `durationMs` already follows in
    `buildHistoryToMessages`, applied to the other thing nobody wrote down."""
    step = _opened()
    # Two builds on screen, one time between them: the stamped run has one and the older run has
    # NO element at all, which is what stops an invented "just now" appearing under it.
    assert step["drawer"]["runs"] == 2
    assert len(step["drawer"]["times"]) == 1
    assert step["drawer"]["times"][0].endswith("ago")


# ---- criterion 2, and the late response that will not show up by clicking around ----------------


@needs_node
def test_switching_app_switches_the_history_to_that_app():
    """The list follows the header WHILE IT IS OPEN, which is the version of this criterion that
    can fail. Two separate opens would pass on an implementation that never dropped anything —
    opening reads — so the selection moves here with the drawer already on screen and showing the
    app it is about to leave. Nobody clicks: a second tab choosing another app moves the server's
    selection and the 30s poll brings it here (#95)."""
    step = _run([{"history": "thr_many", "select": "app_c", "moveTo": "app_a"}])[-1]
    assert step["mid"]["title"] == "Build history · Rate curve viewer"
    assert step["mid"]["prompts"] == ["Draw the rate curve"]
    # Same drawer, never reopened, now the other app's builds under the other app's name.
    assert step["app"] == "app_a"
    assert step["drawer"]["title"] == "Build history · Desk dashboard"
    assert step["drawer"]["prompts"] == ["Sort the desks by P&L", "Add a margin column"]


@needs_node
def test_each_app_opens_on_its_own_builds():
    """The same criterion from a cold start, so "it followed the switch" is not the only reading —
    two apps in one Project, opened one after the other, never share a list."""
    steps = _run([
        {"history": "thr_many", "select": "app_a"},
        {"history": "thr_many", "select": "app_c"},
    ])
    assert steps[0]["drawer"]["prompts"] == ["Sort the desks by P&L", "Add a margin column"]
    assert steps[1]["drawer"]["prompts"] == ["Draw the rate curve"]


def _late_response() -> dict:
    """A read of `app_c`'s log, issued while `app_c` is selected and landing AFTER a poll has moved
    the selection to `app_a`. The route carries no app id, so what is in flight is `app_c`'s
    builds — and it resolves last, which is the only reason it used to win."""
    return _run([{"history": "thr_many", "select": "app_c", "switchTo": "app_a"}])[-1]


@needs_node
def test_a_history_read_that_lands_after_the_app_moved_does_not_paint():
    """The exact bug #101 exists to prevent, arriving through a new door. `mid` is the drawer the
    moment the stale answer landed: the app you left is not in it, in words or in rows."""
    step = _late_response()
    assert step["app"] == "app_a"
    assert step["mid"]["runs"] == 0
    assert step["mid"]["prompts"] == []
    assert "Draw the rate curve" not in " ".join(step["mid"]["words"])
    # Named, so a stale list would be a wrong pairing said out loud rather than inferred.
    assert step["mid"]["title"] == "Build history · Desk dashboard"


@needs_node
def test_the_drawer_reads_the_new_apps_history_rather_than_sitting_on_the_dropped_one():
    """Dropping the stale answer is half of it. A drawer that then showed a skeleton forever would
    be a dead end reached by moving the selection under an open drawer, which needs nobody to
    click."""
    step = _late_response()
    assert step["mid"]["skeletons"] == 1
    assert step["drawer"]["prompts"] == ["Sort the desks by P&L", "Add a margin column"]
    assert step["drawer"]["skeletons"] == 0


# ---- criterion 3, what it is and what it is called ----------------------------------------------


@needs_node
def test_it_is_the_apps_builds_and_not_the_conversations_turns():
    """The substantive half of the criterion, not a word check. `thr_many` is the open Conversation
    and `Sort the desks by P&L` was asked for in `thr_two` — so a list that was the Conversation's
    turns could not be holding it."""
    step = _opened(thread="thr_many")
    assert "Sort the desks by P&L" in step["drawer"]["prompts"]
    # And nothing on the wire narrowed it to one, which is what makes that possible.
    assert "GET /project/history" in step["calls"]


@needs_node
def test_nothing_in_the_drawer_claims_the_conversation():
    """The labelling half. The surface is headed by the APP, and the one sentence that mentions
    conversations at all says these builds came from other ones — which denies the reading the
    criterion rules out rather than inviting it."""
    step = _opened()
    said = " ".join(step["drawer"]["words"]).lower()
    for claim in ("this conversation", "your conversation", "conversation history", "transcript"):
        assert claim not in said, claim
    assert step["drawer"]["title"].startswith("Build history · ")


# ---- criterion 4, it does not displace the preview ----------------------------------------------


@needs_node
def test_the_preview_is_still_there_behind_it():
    """A tab would make the history exclusive with the app it is checked against — #86's reason for
    refusing a Plan tab, unchanged. An overlay does not: the preview and the transcript are both
    still mounted while the history is open, and closing it gives them back."""
    step = _opened()
    assert step["drawer"]["open"] is True
    assert step["previewFrames"] == 1
    assert step["transcripts"] == 1


@needs_node
def test_it_has_a_backdrop_an_x_and_an_escape_key():
    """Three separate props, so a drawer can be missing any one of them and look identical
    otherwise. Named rather than left to antd's defaults, because "there is a way out" is the
    criterion and a default is not a claim."""
    drawer = _opened()["drawer"]
    assert drawer["mask"] is True
    assert drawer["maskClosable"] is True
    assert drawer["closable"] is True
    assert drawer["keyboard"] is True


@needs_node
def test_a_builds_turns_are_behind_its_entry_rather_than_listed_beside_it():
    """One entry per run is the shape, and the turns are how the run was answered. They are drawn
    by the transcript's own reader, in place, so a build you want to look into is not a dead end —
    and they are folded until asked for, so the list stays a list."""
    shut = _opened()
    open_ = _run([{"history": "thr_many", "select": "app_a", "expand": True}])[-1]["drawer"]
    assert shut["drawer"]["turns"] == 0
    assert shut["drawer"]["folds"] == ["Show the 2 turns", "Show the 2 turns"]
    # Two runs of two turns each, and the control says so once it is open.
    assert open_["turns"] == 4
    assert open_["folds"] == ["Hide the turns", "Hide the turns"]
    assert open_["runs"] == 2


# ---- a read that failed is not an app with no builds --------------------------------------------


@needs_node
def test_a_failed_read_says_so_instead_of_claiming_the_app_has_no_builds():
    """The rule `loadAppList` already states about `apps()`: a read that FAILED is not an empty
    answer, and `[]` cannot tell the two apart. Flattened, a 500 would tell somebody with a month
    of builds that they have none — an empty state is a confident claim about the app, and this
    one would be false."""
    step = _run([{"history": "thr_many", "select": "app_a", "readFails": True}])[-1]
    said = " ".join(step["drawer"]["words"])
    assert "Couldn’t read this app’s build log" in said
    assert "No builds of Desk dashboard yet" not in said
    assert step["drawer"]["runs"] == 0


@needs_node
def test_a_failed_read_leaves_a_way_back():
    """A button here rather than "reload the page", on #90's precedent: the person has just been
    told the thing they asked for did not arrive, and sending them hunting for the fix is what
    makes it a dead end."""
    step = _run([{"history": "thr_many", "select": "app_a", "readFails": True}])[-1]
    assert step["drawer"]["buttons"] == ["Try again"]
    # Still a drawer with every way out it had, so the failure does not trap anyone in it either.
    assert step["drawer"]["closable"] is True
    assert step["drawer"]["keyboard"] is True


@needs_node
def test_opening_it_again_reads_again_rather_than_showing_the_last_look():
    """Read on demand buys nothing if the demand is only honoured once. Nothing drops the list when
    the drawer merely closes — the selection never moved, so the app-scope gate has no reason to —
    so re-opening on a cached list would show a history missing every build that finished since it
    was last read. That is the one claim a surface called "every build of this app" cannot survive,
    and `openBuildHistory` clearing on the way in is what keeps it."""
    step = _run([{"history": "thr_many", "select": "app_a", "reopen": True}])[-1]

    assert step["mid"]["runs"] > 0                     # the first look had the list
    assert "GET /project/history" in step["calls"]     # and the second look went and asked again
    assert step["drawer"]["open"] is True
    assert step["drawer"]["prompts"] == step["mid"]["prompts"]


@needs_node
def test_a_shut_drawer_reads_nothing():
    """The log reaches megabytes on a long-lived app, so it is read when somebody asks to see it
    and not on every load. Build's own transcript read is still made — that is #56's, per
    conversation — and this is the read that is not."""
    step = _run([{"history": "thr_many", "select": "app_c", "closed": True}])[-1]
    assert step["drawer"]["open"] is False
    assert "GET /project/history" not in step["calls"]
    assert "GET /project/history?conversation=thr_many" in step["calls"]


# ---- criterion 5 --------------------------------------------------------------------------------


def test_nothing_in_this_surface_reads_the_conversation_view_preference():
    """A real constraint rather than boilerplate: reading it here would tie the build history to an
    A/B arm that is about to lose one half (#61). The drawer is one file and the store's reader is
    one function, so both can be asked outright."""
    assert "conversationView" not in _DRAWER.read_text()
    src = _STORE.read_text()
    start = src.index("async function loadAppHistory(")
    assert "conversationView" not in src[start : src.index("\n  }\n", start)]
    assert "conversationView" not in _API.read_text()


# ---- the payload nobody asked for, and the one entry that asks for it ---------------------------


def test_the_api_entry_names_no_conversation():
    """The rule that makes this the other question. `history(conversation)` is Build's transcript,
    per conversation (ADR-0005); `appHistory()` is the app's whole log, and a conversation on the
    wire would silently turn it back into the first one."""
    src = _API.read_text()
    start = src.index("appHistory:")
    entry = src[start : src.index("\n", src.index("request(", start))]
    assert "'/project/history'" in entry
    assert "conversation" not in entry


@needs_node
def test_the_run_grouping_is_shared_with_the_transcript_rather_than_copied():
    """Chat's merged view folds a build run the same way, and one answer to "where does a build
    start and stop" is the point. The drawer reads `SW.buildRuns`, which IS `buildRunMessages`."""
    src = _STORE.read_text()
    assert "SW.buildRuns = buildRunMessages;" in src
    assert "SW.buildRuns(" in _DRAWER.read_text()
    # And the drawer defines no grouping of its own — a copy would need one of these.
    assert "type === 'user'" not in _DRAWER.read_text()
