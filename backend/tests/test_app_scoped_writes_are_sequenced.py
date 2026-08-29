"""App-scoped writes are sequenced, so a slow read cannot overwrite a newer one (#101).

`activeApp`, `bindings`, `appAttachments` and `appRemoval` describe ONE app between them, and
eleven functions in `store.js` wrote them at `ee24c31`. The async ones read in parallel and none
knew about the others, so whichever RESOLVED last won — which is not the same as whichever STARTED last.
A `/bindings` read taken under the app you left could land on top of a fresh one and print the
old app's records under the new app's name: the wrong pairing #95 fixed, arrived at by timing
rather than by a missing refresh.

WHY A LOCAL COUNTER DOES NOT DO IT. #95's reviewer proposed a generation counter local to
`refreshAppScope` — bump on entry, drop the answer if it moved. It only ever sees that function's
own overlapping calls, and the competing write comes from `loadBuild`'s `refreshBindings`, which
would never touch it. So the counter is shared and the writers are enumerated: nothing assigns
these four fields directly any more, which is the half a local counter cannot cover.

HOW IT ORDERS. A read is ordered by when it STARTED, so the newer of two reads wins however they
resolve. An ACT — the two removals, which write the manifest the route just answered with — takes
its place at the head of the queue instead, because the server has already written what it carries
and a read that started first would put back what has just gone. That costs an act the sequence's
protection against an app switch, and the generation buys it back: the act's list is right, but by
then it can be another app's list.

PER FIELD, NOT ONE HIGH-WATER MARK. Sharing one number across the four would make every writer
supersede every other, including writers that never competed: the 2s build tick writes `activeApp`
alone, and clicking Dismiss writes the notice alone, and neither may throw away a `/bindings` read
still in flight for the same app.

WHAT IS NOT IN IT. `composerSeed` is not app-scoped — it is a draft handed to the composer and
cleared on read — so the gate does not cover it. Nor is `buildHistoryOpen` (#88): switching app
changes WHICH builds are listed, never whether somebody had asked to see them.

The out-of-order resolve is the test: each race below asserts the NEWER write won, not merely that
one of them did. Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_STORE = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "store.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

# The four fields, and the ten writers that now reach the gate. The audit of `ee24c31` found
# eleven: `reportRemoval` was the eleventh, and it no longer writes — it returns its notice as
# `removalNotice` for its caller to install, which the no-direct-assignment test below covers.
# Named here rather than counted, because the first criterion is that the writers are ENUMERATED:
# a writer added later has to be added to this list to be covered, and the gate test below is what
# makes forgetting it fail rather than pass quietly.
#
# `appHistory` joined them in #88: the app's whole build log, read over a route that carries no app
# id, which makes a late answer describe an app that is no longer on screen. Its own tests live in
# `test_build_shows_the_apps_build_history.py`; what belongs here is that the gate covers it and
# that its two writers go through the gate like the rest.
APP_SCOPED = ("activeApp", "bindings", "appAttachments", "appRemoval", "appHistory")
WRITERS = (
    "async function loadScopeData(",
    "async function loadAppList(",
    "async function refreshAppScope(",
    "async function refreshBindings(",
    "async function loadAppHistory(",
    "async setScope(",
    "async removeBindingFromApp(",
    "async removeAttachmentFromApp(",
    "dismissAppRemoval(",
    "clearApp(",
    "openBuildHistory(",
    "async loadBuild(",
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


def _body_from(src: str, decl: str) -> str:
    """One declaration's body, matched on braces, so a claim about a function is about it alone.

    The parameter list is walked on parens rather than jumped over: a destructured parameter with
    a default — `loadAppList({ cascade = true, ticket = appScopeTicket() } = {})` — holds both
    kinds of bracket, and the first `)` after the name is nowhere near the end of it.
    """
    start = src.index(decl)
    params = src.index("(", start)
    depth = 0
    for close in range(params, len(src)):
        depth += {"(": 1, ")": -1}.get(src[close], 0)
        if depth == 0:
            break
    i = src.index("{", close)
    depth = 0
    for end in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[end], 0)
        if depth == 0:
            return src[i : end + 1]
    raise AssertionError(f"{decl} is not closed")


# ---- the enumeration -----------------------------------------------------------------------


def test_nothing_writes_app_scoped_state_except_the_gate():
    """The criterion is one generation shared across every path, and the only way to hold that is
    to leave no path around it. Every direct assignment is gone; the one left is the gate's own."""
    src = _STORE.read_text()
    gate = _body_from(src, "function applyAppScope(")
    outside = src.replace(gate, "")
    for field in APP_SCOPED:
        assert not re.search(rf"state\.{field}\s*=[^=]", outside), field


def test_every_writer_the_audit_found_goes_through_the_gate():
    """Enumerated rather than assumed. Each of these wrote one of the four fields by hand at
    `ee24c31`; each has to reach the store through `applyAppScope` now, and a writer that stopped
    being one — or was renamed out from under this list — fails here rather than silently."""
    src = _STORE.read_text()
    for decl in WRITERS:
        assert decl in src, f"{decl} is gone — the audit is out of date"
        assert "applyAppScope(" in _body_from(src, decl), decl


def test_the_gate_covers_the_app_scoped_fields_and_not_the_ones_that_belong_to_the_person():
    """`composerSeed` is a draft handed to the composer and cleared on read. It belongs to the
    person, not to the app, and sequencing it against app-scoped reads would drop prompts.
    `buildHistoryOpen` is the same kind of thing: whether the build history is on screen is the
    person's answer, and only the list inside it is the app's (#88)."""
    src = _STORE.read_text()
    listed = re.search(r"const APP_SCOPED = \[([^\]]*)\]", src)
    assert listed, "the gate no longer names the fields it covers"
    names = re.findall(r"'([^']+)'", listed.group(1))
    assert sorted(names) == sorted(APP_SCOPED)
    assert "composerSeed" not in names
    assert "buildHistoryOpen" not in names


# ---- a read that started first, landing last -------------------------------------------------


def _read_then_switch() -> dict:
    """A build read taken under `app_c`, landing after a poll has moved the selection to `app_a`.

    Both apps carry records — `app_c` one Binding and no files, `app_a` three Bindings and two
    files — so every list changes VALUE rather than merely appearing or clearing, and an
    implementation that blanked instead of sequencing would not pass by accident.
    """
    return _run(
        [{"race": "read-then-switch", "thread": "thr_many", "select": "app_c", "raceTo": "app_a"}]
    )[-1]


@needs_node
def test_the_newer_write_wins_however_the_two_resolve():
    """The whole of #101. The stale read resolves LAST and used to win for that reason alone."""
    step = _read_then_switch()
    assert step["activeApp"] == "app_a"
    assert step["bindings"] == ["Claude Sonnet 4", "Market data EOD", "Churn risk"]
    assert step["attachments"] == ["margins.csv", "legacy.csv"]


@needs_node
def test_the_stale_read_is_dropped_rather_than_repaired_by_a_later_one():
    """Dropped, not corrected afterwards: nothing re-reads once the loser lands, so if the stale
    write went in it would sit on screen until the next poll 30 seconds later."""
    step = _read_then_switch()
    # The reads the race made, and no others. A fix that recovered by fetching again would show up
    # here as a second `/bindings` after the last `/apps`.
    assert step["calls"].count("GET /bindings") == 2
    assert step["calls"].count("GET /project") == 2


@needs_node
def test_the_header_row_names_the_new_app_over_the_new_apps_records():
    """#92's row heads its lists with the app name outright, so the losing write is not an
    ambiguous stale list — it is a named wrong pairing, printed in words."""
    step = _read_then_switch()
    said = " ".join(
        t for p in step["parts"] if p["className"].startswith("sw-app-scope") for t in p["texts"]
    )
    assert "Desk dashboard" in said
    assert "Market data EOD" in said
    assert "margins.csv" in said
    assert "Qwen 2.5" not in said


# ---- an act, against a read that started before it --------------------------------------------


@needs_node
def test_an_acts_own_result_does_not_lose_to_a_read_that_started_first():
    """`removeBindingFromApp` writes the manifest the route just wrote, not a read of one. A
    `/bindings` read taken before the unbind and landing after it carries the Binding that has
    gone, and installing it would put a removed Binding back on the panel."""
    step = _run(
        [
            {
                "race": "read-then-act",
                "thread": "thr_many",
                "select": "app_a",
                "remove": "Market data EOD",
            }
        ]
    )[-1]
    assert step["bindings"] == ["Claude Sonnet 4", "Churn risk"]
    assert step["notice"].startswith("Market data EOD is out of Desk dashboard.")


@needs_node
def test_an_act_whose_app_was_left_does_not_write_over_the_app_that_replaced_it():
    """The other side of the same rule. Claiming the head of the queue is what keeps an act ahead
    of a read; it must not also make it beat the app switch that happened while the route was
    answering, or the act would print `app_a`'s Bindings under `app_c`'s name."""
    step = _run(
        [
            {
                "race": "act-then-switch",
                "thread": "thr_many",
                "select": "app_a",
                "remove": "Market data EOD",
                "raceTo": "app_c",
            }
        ]
    )[-1]
    assert step["activeApp"] == "app_c"
    assert step["bindings"] == ["Qwen 2.5"]
    # The notice names its own app in its own sentence, so a surviving one is the wrong pairing
    # said out loud rather than inferred from a list.
    assert step["notice"] is None


# ---- the notice, which is app-scoped too -------------------------------------------------------


@needs_node
def test_the_notice_goes_when_the_selection_moves_however_it_moved():
    """`refreshAppScope` cleared `appRemoval` by hand for this issue's exact reason, and covered
    one of the four paths the selection moves down. A hand-made app switch goes through
    `loadBuild`, whose `loadAppList({cascade: false})` sets the app without ever reaching that
    clear — so the notice stayed, under a head naming a different app than its sentence does.
    The gate clears it wherever the selection actually moves, so the hand-written clear is gone."""
    step = _run(
        [
            {
                "race": "remove-then-switch",
                "thread": "thr_many",
                "select": "app_a",
                "remove": "Market data EOD",
                "raceTo": "app_c",
            }
        ]
    )[-1]
    assert step["noticeBefore"].startswith("Market data EOD is out of Desk dashboard.")
    assert step["activeApp"] == "app_c"
    assert step["notice"] is None


@needs_node
def test_dismissing_the_notice_does_not_drop_a_read_in_flight_behind_it():
    """The queue orders what was READ, and the notice is not read — an act writes it and the
    person clears it. A Dismiss that took a place in the queue would be the newest write on
    screen, and the `/bindings` read still out behind it would be thrown away for having started
    first: the panel a poll behind, from a click that was about one sentence."""
    step = _run(
        [
            {
                "race": "dismiss-mid-read",
                "thread": "thr_many",
                "select": "app_a",
                "remove": "Market data EOD",
            }
        ]
    )[-1]
    assert step["noticeBefore"].startswith("Market data EOD is out of Desk dashboard.")
    assert step["notice"] is None
    # What the read was carrying: a Binding a second tab added while the notice was on screen.
    assert step["bindings"] == ["Claude Sonnet 4", "Churn risk", "GPT OSS 120B"]


def _read_then_tick() -> dict:
    """A read of one app's Bindings, with the 2s build tick landing on top of it — same app, no
    act, no switch. The tick writes `activeApp` and nothing else, so the two do not compete for
    anything; a shared high-water mark would have them compete anyway."""
    return _run(
        [
            {
                "race": "read-then-tick",
                "thread": "thr_many",
                "select": "app_a",
                "remove": "Market data EOD",
            }
        ]
    )[-1]


@needs_node
def test_a_tick_that_writes_only_the_selected_app_keeps_a_read_of_its_lists():
    """The build tick runs every 2 seconds for the length of a build and calls `loadAppList`. If
    writing `activeApp` superseded a `/bindings` read in flight for the SAME app, the panel would
    lose a good answer with nothing re-reading until the build ended."""
    step = _read_then_tick()
    # What the read was carrying: a Binding a second tab added while the read was out.
    assert step["bindings"] == ["Claude Sonnet 4", "Churn risk", "GPT OSS 120B"]
    assert step["activeApp"] == "app_a"


@needs_node
def test_the_notice_stays_through_a_refresh_that_does_not_move_the_app():
    """The rule is the selection MOVING, not a refresh happening. `refreshAppScope` used to clear
    the notice on every call, so a refresh that landed on the same app took it away mid-read — and
    a file list is exactly what ADR-0011 says five seconds is not long enough to read."""
    step = _read_then_tick()
    assert step["noticeBefore"].startswith("Market data EOD is out of Desk dashboard.")
    assert step["notice"] == step["noticeBefore"]


# ---- what the single-writer case costs ---------------------------------------------------------


@needs_node
def test_a_switch_with_nothing_racing_it_reads_each_record_once_and_installs_it():
    """Sequencing costs the ordinary case nothing: not a request, and not a write dropped for
    having been ticketed. Both halves are asserted, because a gate that dropped everything would
    pass the request count on its own."""
    step = _run([{"thread": "thr_many", "select": "app_c", "switchTo": "app_a"}])[-1]
    assert step["calls"].count("GET /bindings") == 1
    assert step["calls"].count("GET /project") == 1
    assert step["activeApp"] == "app_a"
    assert step["bindings"] == ["Claude Sonnet 4", "Market data EOD", "Churn risk"]
    assert step["attachments"] == ["margins.csv", "legacy.csv"]


@needs_node
def test_a_poll_that_changes_nothing_still_costs_the_one_read_it_always_cost():
    """The tick that loses the race stops at its own read rather than cascading into two more it
    would only throw away — and the tick that changes nothing is unaffected by any of this."""
    steps = _run(
        [
            {"thread": "thr_many", "select": "app_a", "poll": "app_a"},
            {"thread": "thr_many", "select": "app_c", "poll": "app_a"},
        ]
    )
    assert steps[0]["calls"] == ["GET /apps"]
    assert steps[1]["calls"] == ["GET /apps", "GET /project", "GET /bindings"]
